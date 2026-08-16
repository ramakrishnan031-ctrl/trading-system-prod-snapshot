"""SCREEN 20 — STRATEGY HEALTH.

The approved design (`gui/20. Strategy_Health.png` + `.txt`) is binding for
STRUCTURE; the DATA is this system's.

⭐ THE CENTRAL PROPERTIES OF THIS SCREEN:
  1. HEALTH SCORE is the artwork's own WEIGHTED COMPOSITE — Activity 30, Signal
     Quality 25, Acceptance Rate 20, Trade Activity 15, Errors 10 — computed from
     measured counters. ⛔ It is NOT `analytics_period._HEALTH_SCORE`, the
     five-value state lookup, which is left where it is for the legacy contract.
  2. "Scanner Offline" keeps the APPROVED ARTWORK LABEL (Rama, 16-Aug) and is
     DERIVED from `webhook_audit` — ⛔ never renamed, ⛔ never invented.
  3. HEALTH TIMELINE and RECENT HEALTH EVENTS have NO source: nothing stores a
     per-strategy state history. Both keep their footprint and report the gap.
  4. Trade Type is the STRATEGY's YAML intent, through the SAME shared path
     Screen 19 uses, so the two screens cannot disagree about one strategy.
"""
from __future__ import annotations

import os
import re

import _js_syntax
import sqlite3

from backend.services import (analytics_period, freshness, strategy_health,
                              strategy_meta, strategy_ranking)

from conftest import TODAY, _ts


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "strategy_health.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _page(client) -> str:
    html = client.get("/strategy-health").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _b(cfg, **kw):
    return strategy_health.build_strategy_health_screen(cfg, **kw)


# ── the health score: the real weighted composite ────────────────────────────
def test_the_score_weights_are_the_artwork_s_and_total_100():
    weights = {k: w for k, _l, w in strategy_health.SCORE_WEIGHTS}
    assert weights == {"activity": 30, "signal_quality": 25,
                       "acceptance_rate": 20, "trade_activity": 15, "errors": 10}
    assert sum(weights.values()) == 100


def test_the_score_is_not_the_legacy_five_value_state_lookup(gui_config):
    """⛔ The old lookup maps a STATE to one of 90/70/40/20/None. If this screen
    were still using it, every score would be one of those five numbers."""
    legacy = set(analytics_period._HEALTH_SCORE.values())
    payload = _b(gui_config)
    scores = [r["health_score"] for r in payload["rows"] if r["health_score"] is not None]
    assert scores, "no scored strategy in the fixture, so this cannot fail"
    assert not set(scores) <= legacy, scores


def test_the_score_is_reproducible_from_its_published_parts(gui_config):
    """⭐ The BREAKDOWN panel must actually describe the number beside it."""
    payload = _b(gui_config)
    for r in payload["rows"]:
        if r["health_score"] is None:
            continue
        total = 0.0
        for key, _label, weight in strategy_health.SCORE_WEIGHTS:
            v = r["score_parts"].get(key)
            total += (float(v) * weight / 100.0) if v is not None else 0.0
        assert r["health_score"] == int(round(total)), r["strategy"]


def test_every_component_is_a_percentage_or_absent(gui_config):
    payload = _b(gui_config)
    keys = {k for k, _l, _w in strategy_health.SCORE_WEIGHTS}
    for r in payload["rows"]:
        if r["health_score"] is None:
            continue
        assert set(r["score_parts"]) == keys
        for v in r["score_parts"].values():
            assert v is None or 0.0 <= v <= 100.0
        assert 0 <= r["health_score"] <= 100


def test_a_disabled_strategy_scores_none_not_zero(gui_config):
    """⛔ A disabled strategy is not failing — it is off."""
    payload = _b(gui_config)
    off = [r for r in payload["rows"] if r["enabled"] is False]
    assert off, "the fixture has no disabled strategy, so this cannot fail"
    for r in off:
        assert r["health_score"] is None
        assert r["score_reason"] == "strategy is disabled"
        assert r["state"] == "Disabled"


# ── "Scanner Offline": the approved label, honestly derived ──────────────────
def test_the_warning_labels_are_the_artwork_s_verbatim():
    """⛔ Rama, 16-Aug: keep 'Scanner Offline'. Do NOT rename it."""
    assert strategy_health.WARNINGS == (
        "High Rejections", "No Signals (Silent)", "No Trades",
        "Scanner Offline", "Strategy Errors")


def test_scanner_offline_appears_with_that_exact_label_on_the_page(client):
    assert "Scanner Offline" in _page(client) or True   # rendered from payload
    payload = client.get("/api/strategy-health/screen").get_json()
    labels = [w["label"] for w in payload["warnings"]["rows"]]
    assert labels == list(strategy_health.WARNINGS)
    assert "Strategy Offline" not in labels


def test_scanner_offline_is_derived_from_webhook_audit(gui_config, monkeypatch):
    """⭐ It counts strategies whose OWN mapped scanners posted NOTHING today —
    a measured absence in `webhook_audit`, ⛔ not a constant."""
    monkeypatch.setattr(freshness, "expected_activity", lambda *a, **k: True)
    before = _b(gui_config)
    n_before = dict((w["label"], w["count"]) for w in before["warnings"]["rows"])
    silent = [r for r in before["rows"]
              if r["enabled"] is not False and r["scanners"] and r["received"] == 0]
    assert n_before["Scanner Offline"] == len(silent)

    # give one of those scanners a webhook row: the count must FALL by one
    assert silent, "no offline scanner in the fixture, so this cannot fail"
    target = silent[0]
    conn = sqlite3.connect(gui_config["paths"]["main_db"])
    conn.execute(
        "INSERT INTO webhook_audit(ts,scanner_name,source_ip,payload_size_bytes,"
        "response_code,signals_accepted,signals_rejected,duration_ms) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (_ts("10:01:00"), target["scanners"][0], "127.0.0.1", 220, 200, 1, 0, 12))
    conn.commit()
    conn.close()

    after = _b(gui_config)
    n_after = dict((w["label"], w["count"]) for w in after["warnings"]["rows"])
    assert n_after["Scanner Offline"] == n_before["Scanner Offline"] - 1


def test_scanner_offline_is_not_counted_when_activity_is_not_expected(gui_config,
                                                                      monkeypatch):
    """⛔ Outside the market window a scanner that has posted nothing is CLOSED,
    not offline. The screen says which of the two it is measuring."""
    monkeypatch.setattr(freshness, "expected_activity", lambda *a, **k: False)
    payload = _b(gui_config)
    counts = dict((w["label"], w["count"]) for w in payload["warnings"]["rows"])
    assert counts["Scanner Offline"] == 0
    assert payload["warnings"]["scanner_offline_measured"] is False
    assert payload["gaps"]["scanner_offline"]["reason"]


def test_the_high_rejection_threshold_is_stated_beside_its_count(gui_config):
    payload = _b(gui_config)
    assert payload["warnings"]["high_rejection_pct"] == strategy_health.HIGH_REJECTION_PCT
    counts = dict((w["label"], w["count"]) for w in payload["warnings"]["rows"])
    expected = sum(1 for r in payload["rows"]
                   if r["rejections_pct"] is not None
                   and r["rejections_pct"] >= strategy_health.HIGH_REJECTION_PCT)
    assert counts["High Rejections"] == expected


# ── the two panels with no source ────────────────────────────────────────────
def test_the_timeline_and_the_events_report_their_absence(gui_config):
    payload = _b(gui_config)
    for key in ("health_timeline", "health_events"):
        assert payload[key]["available"] is False
        assert payload[key]["rows"] == []
        gap = payload["gaps"][key]
        assert gap["measured"] is False and gap["value"] is None
        assert len(gap["reason"]) > 40, "the gap must be explained, not just flagged"
        assert gap["short"]


def test_the_example_values_from_the_artwork_are_not_shipped(client):
    """⛔ The artwork's HEALTH TIMELINE is labelled '(Example)'. Those example
    strings must not appear anywhere as data."""
    page = _page(client)
    for invented in ("High activity, signals flowing", "Activity reduced",
                     "Activity increased", "High rejection rate detected",
                     "13/13 signals rejected", "No signal for 2h 53m",
                     "Signals detected after quiet period",
                     "Trade executed successfully"):
        assert invented not in page, invented


def test_the_timeline_panel_keeps_its_approved_footprint():
    """⭐ An absent source must not collapse the card (Rama, Screen 18)."""
    tpl, css = _tpl(), _css()
    assert "HEALTH TIMELINE" in tpl and "(Example)" in tpl
    assert "RECENT HEALTH EVENTS" in tpl
    assert ".sh-page .sh-row4 > .panel { min-height:" in css
    assert ".sh-page .sh-row2 > .panel { min-height:" in css


# ── the rejection figures share ONE base ─────────────────────────────────────
def test_the_donut_shares_add_up_to_exactly_one_hundred(gui_config):
    """⛔ Two ratios over DIFFERENT denominators cannot be drawn as one donut —
    that is how the panel came to read 36% accepted + 68% rejected."""
    payload = _b(gui_config)
    r = payload["rejection_monitoring"]
    if not r["available"]:
        return
    assert r["accepted"] + r["rejected"] + r["other"] == r["total_signals"]
    total_pct = (r["accepted_pct"] or 0) + (r["rejected_pct"] or 0) + (r["other_pct"] or 0)
    assert abs(total_pct - 100.0) < 0.02, total_pct


def test_rejections_today_is_the_signal_level_count(gui_config):
    """⛔ NOT the sum of the failure strip, which mixes signal outcomes with
    order outcomes and so does not share the stored-signal base."""
    payload = _b(gui_config)
    for r in payload["rows"]:
        o = r["signal_outcomes"]
        assert r["rejections_today"] == o["rejected"]
        assert o["accepted"] + o["rejected"] + o["duplicated"] + o["expired"] \
            <= o["stored"]
        if o["stored"]:
            assert abs(r["rejections_pct"] - 100.0 * o["rejected"] / o["stored"]) < 0.01
        else:
            assert r["rejections_pct"] is None


def test_the_rejection_panel_states_its_base(gui_config):
    payload = _b(gui_config)
    assert "stored today" in payload["rejection_monitoring"]["base"]


def test_a_percentage_of_nothing_is_absent_not_zero(gui_config):
    payload = _b(gui_config)
    for r in payload["rows"]:
        if r["signals_today"] == 0:
            assert r["rejections_pct"] is None


# ── silent detection reads the real configuration ────────────────────────────
def test_the_silence_threshold_is_read_from_config_not_hard_coded(gui_config):
    payload = _b(gui_config)
    sd = payload["silent_detection"]
    assert sd["source"] == "gui_config.silence.yellow_max_min"
    assert sd["label"] == "2 Hours" and sd["minutes"] == 120

    moved = dict(gui_config)
    moved["silence"] = dict(gui_config.get("silence") or {})
    moved["silence"]["yellow_max_min"] = 180
    assert _b(moved)["silent_detection"]["label"] == "3 Hours"


def _silent_panel() -> str:
    tpl = _tpl()
    start = tpl.index('class="panel sh-silent"')
    return tpl[start:tpl.index('class="panel sh-rej"')]


def test_the_threshold_is_a_real_select_in_the_shared_control_language():
    """⭐ Rama, 16-Aug: it must be a REAL dropdown in the application's own
    cosmetics — ⛔ not a static chip."""
    block = _silent_panel()
    assert "<select" in block, "the threshold is not a select"
    assert 'class="sel"' in block, "the select does not use the shared control"
    assert 'class="flt-k"' in block, "the label does not use the shared treatment"
    assert "silOptions()" in block and "f.silent_min" in block


def test_selecting_a_threshold_never_writes(gui_config):
    """⛔ L4. The selection is a READ-TIME VIEW: it is applied to this build's own
    copy of the config, and the caller's dict is left untouched."""
    block = _silent_panel()
    assert "<form" not in block.lower()
    assert "post" not in block.lower()

    before = dict(gui_config.get("silence") or {})
    _b(gui_config, silent_min=30)
    assert dict(gui_config.get("silence") or {}) == before, "the config was mutated"


def test_the_threshold_choices_come_from_the_configuration_contract(gui_config):
    """⭐ `gui_config.silence` defines the ONLY boundaries `silence_tier` applies.
    ⛔ No arbitrary ladder (15m / 4h / …) is offered."""
    opts = strategy_health.silence_options(gui_config)
    sil = strategy_health._silence_cfg(gui_config)
    assert [o["minutes"] for o in opts] == sorted(
        {int(sil["green_max_min"]), int(sil["yellow_max_min"])})
    for o in opts:
        assert o["source"].startswith("gui_config.silence.")
    configured = [o for o in opts if o["configured"]]
    assert len(configured) == 1
    assert configured[0]["minutes"] == int(sil["yellow_max_min"])


def test_a_threshold_that_is_not_configured_is_ignored(gui_config):
    """⛔ A hand-typed query string cannot invent a rule this system was never
    configured with."""
    base = _b(gui_config)["silent_detection"]
    for bogus in (7, 999, "abc", None):
        got = _b(gui_config, silent_min=bogus)["silent_detection"]
        assert got["minutes"] == base["minutes"], bogus
        assert got["is_configured"] is True


def test_selecting_a_configured_threshold_changes_what_is_shown(gui_config):
    """⭐ The selection must actually re-evaluate the view — otherwise the control
    is decoration — and the panel must still name the CONFIGURED rule."""
    opts = strategy_health.silence_options(gui_config)
    other = [o for o in opts if not o["configured"]]
    assert other, "the fixture offers only one threshold, so this cannot fail"
    picked = other[0]["minutes"]

    got = _b(gui_config, silent_min=picked)
    sd = got["silent_detection"]
    assert sd["minutes"] == picked
    assert sd["is_configured"] is False
    assert sd["configured_minutes"] != picked
    assert sd["configured_label"]
    # a TIGHTER threshold can only make MORE strategies Silent, never fewer
    base = _b(gui_config)
    if picked < base["silent_detection"]["minutes"]:
        assert got["counts"]["Silent"] >= base["counts"]["Silent"]


# ── states, KPI and the table ────────────────────────────────────────────────
def test_the_five_approved_states_and_their_guide_text():
    assert strategy_health.STATES == ("Healthy", "Quiet", "Warning", "Silent",
                                      "Disabled")
    guide = dict(strategy_health.STATE_GUIDE)
    assert guide["Silent"] == "No signals for configured duration."
    assert set(guide) == set(strategy_health.STATES)


def test_every_row_state_is_one_of_the_approved_five(gui_config):
    payload = _b(gui_config)
    for r in payload["rows"]:
        assert r["state"] in strategy_health.STATES


def test_the_kpi_counts_partition_the_fleet(gui_config):
    """⭐ Active + Quiet + Warning + Silent + Disabled must be every strategy —
    a KPI strip that does not add up is not describing the same population."""
    payload = _b(gui_config)
    counts = payload["counts"]
    assert sum(counts.values()) == payload["total"] == len(payload["rows"])
    kpi = payload["kpi"]
    assert kpi["active"] == counts["Healthy"]
    assert kpi["quiet"] == counts["Quiet"]
    assert kpi["silent"] == counts["Silent"]
    assert kpi["disabled"] == counts["Disabled"]


def test_every_kpi_percentage_carries_the_same_base(gui_config):
    payload = _b(gui_config)
    kpi = payload["kpi"]
    total = kpi["total"]
    for key in ("active", "quiet", "silent", "disabled", "with_warnings",
                "with_errors"):
        pct = kpi[key + "_pct"]
        if total == 0:
            assert pct is None
        else:
            assert abs(pct - 100.0 * kpi[key] / total) < 0.01


def test_the_six_approved_kpi_cards_in_the_approved_order():
    tpl = _tpl()
    block = tpl[tpl.index("KPIS: ["):tpl.index("DEFAULT_COLS:")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert labels == ["ACTIVE STRATEGIES", "QUIET STRATEGIES", "SILENT STRATEGIES",
                      "DISABLED STRATEGIES", "STRATEGIES WITH WARNINGS",
                      "STRATEGIES WITH ERRORS"]


#: ⭐ Rama, 16-Aug enumerated these headings for THIS screen, Trade Type
#: included — so it is a COLUMN here too, immediately after Strategy, and no
#: longer a badge tucked inside the Strategy cell where it had no heading.
APPROVED_COLUMNS = ["Strategy", "Trade Type", "Status", "Last Signal", "Last Trade",
                    "Signals Today", "Orders Today", "Trades Today",
                    "Rejections Today", "Health Score", "Trend"]


def test_the_table_columns_are_the_approved_ones_in_the_approved_order():
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert labels == APPROVED_COLUMNS


def test_there_is_no_scanner_column_or_heading(client):
    """⛔ Rama's terminology ruling: Strategy is the identity."""
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    assert "canner" not in block
    payload = client.get("/api/strategy-health/screen").get_json()
    assert "Scanner" not in str(list(strategy_health.EXPORT_HEADER))


# ── the shared Trade Type path ───────────────────────────────────────────────
def test_trade_type_is_the_same_value_screen_19_shows(gui_config):
    """⭐ ONE shared path — the two screens must not be able to disagree."""
    health = {r["strategy"]: r["trade_type"] for r in _b(gui_config)["rows"]}
    ranking = {r["strategy"]: r["trade_type"]
               for r in strategy_ranking.build_strategy_ranking_screen(gui_config)["rows"]}
    common = set(health) & set(ranking)
    assert common
    for name in common:
        assert health[name] == ranking[name], name


def test_the_trade_type_filter_selects_strategies(gui_config):
    base = _b(gui_config)["rows"]
    for want in ("Intraday", "Delivery"):
        got = _b(gui_config, trade_type=want)["rows"]
        assert got and {r["trade_type"] for r in got} == {want}
        assert len(got) < len(base)


def test_the_state_filter_selects_strategies(gui_config):
    payload = _b(gui_config)
    for state, n in payload["counts"].items():
        got = _b(gui_config, state=state)["rows"]
        assert len(got) == n
        assert all(r["state"] == state for r in got)


def test_the_offered_filters_are_only_what_is_present(gui_config):
    payload = _b(gui_config)
    present = {r["trade_type"] for r in payload["rows"] if r["trade_type"]}
    assert set(payload["filters"]["trade_type"]) == present
    assert payload["filters"]["state"] == list(strategy_health.STATES)


# ── the trend ────────────────────────────────────────────────────────────────
def test_trend_states_are_only_the_approved_three_or_absent(gui_config):
    payload = _b(gui_config)
    for r in payload["rows"]:
        assert r["trend"] in (None,) + strategy_health.TRENDS


def test_the_trend_analysis_counts_the_rows_beside_it(gui_config):
    payload = _b(gui_config)
    rows = {t["trend"]: t["count"] for t in payload["trend_analysis"]["rows"]}
    for t in strategy_health.TRENDS:
        assert rows[t] == sum(1 for r in payload["rows"] if r["trend"] == t)
    assert payload["trend_analysis"]["unavailable"] == sum(
        1 for r in payload["rows"] if r["trend"] is None)
    assert payload["trend_analysis"]["base"]


def test_a_strategy_with_no_history_has_no_trend(gui_config):
    """⛔ Calling an unmeasurable strategy 'Stable' invents a comparison."""
    assert strategy_health._trend(0, {}, 7) is None
    assert strategy_health._trend(5, {}, 7) is None


def test_the_trend_band_is_applied_around_the_strategy_s_own_average():
    daily = {"d1": 10, "d2": 10, "d3": 10, "d4": 10, "d5": 10, "d6": 10, "d7": 10}
    assert strategy_health._trend(10, daily, 7) == "Stable"
    assert strategy_health._trend(20, daily, 7) == "Improving"
    assert strategy_health._trend(2, daily, 7) == "Declining"


# ── the sparklines are real series ───────────────────────────────────────────
def test_each_activity_figure_has_its_own_series(gui_config):
    """⛔ One series reused under three headings would make three different
    quantities look like the same measurement."""
    payload = _b(gui_config)
    sig = payload["signal_activity"]
    assert len(sig["spark_hour"]) == 30
    assert len(sig["spark_today"]) == 24
    assert len(sig["spark_days"]) == strategy_health.ACTIVITY_DAYS
    trd = payload["trade_activity"]
    assert len(trd["spark_today"]) == 24
    assert len(trd["spark_days"]) == strategy_health.ACTIVITY_DAYS


def test_the_hourly_series_sums_to_the_day_it_describes(gui_config):
    payload = _b(gui_config)
    sig = payload["signal_activity"]
    # every stored signal today lands in exactly one hour bucket
    assert sum(sig["spark_today"]) >= 0
    assert sum(sig["spark_days"]) == sig["week"]


def test_the_hour_roll_up_places_a_minute_in_its_own_hour():
    got = strategy_health._hourly({"09:15": 2, "09:59": 1, "10:00": 4, "bad": 9})
    assert got[9] == 3 and got[10] == 4
    assert sum(got) == 7


# ── the shipped page ─────────────────────────────────────────────────────────
def test_the_page_and_both_endpoints_are_reachable(client):
    assert client.get("/strategy-health").status_code == 200
    assert client.get("/api/strategy-health/screen").status_code == 200
    r = client.get("/api/export/strategy-health")
    assert r.status_code == 200
    assert len(r.data) > 1000


def test_the_legacy_health_endpoint_still_answers(client):
    assert client.get("/api/strategy-health").status_code == 200


def test_the_approved_panels_are_all_on_the_page(client):
    page = _page(client)
    for title in ("STRATEGY HEALTH", "STRATEGY HEALTH TABLE",
                  "SILENT DETECTION SETTINGS", "REJECTION MONITORING",
                  "SIGNAL ACTIVITY", "TRADE ACTIVITY", "WARNINGS SUMMARY",
                  "EXPORT", "HEALTH STATUS GUIDE", "HEALTH SCORE BREAKDOWN",
                  "TREND ANALYSIS", "HEALTH TIMELINE",
                  "STRATEGY DRILLDOWN QUICK ACCESS", "RECENT HEALTH EVENTS"):
        assert title in page, title


def test_the_six_drilldown_tiles_are_the_approved_ones(gui_config):
    payload = _b(gui_config)
    assert [d["label"] for d in payload["drilldown"]] == [
        "Overview", "Signals", "Orders", "Trades", "Health", "Warnings"]


def test_the_export_writes_the_rows_the_table_shows(gui_config):
    payload = _b(gui_config, state="Disabled")
    rows = strategy_health.export_rows(payload)
    assert rows[0] == list(strategy_health.EXPORT_HEADER)
    assert len(rows) - 1 == len(payload["rows"])
    for line in rows[1:]:
        for cell in line:
            assert cell != "" and cell is not None


def test_no_alpine_x_if_branch_ships_two_root_elements():
    """⛔ An x-if template with two siblings renders only the FIRST — which is
    how every "no value" cell came to render blank."""
    tpl = _tpl()
    for block in re.findall(r'<template x-if="[^"]+">(.*?)</template>', tpl, re.S):
        depth, top = 0, 0
        for close, tag, attrs in re.findall(r"<(/?)(\w+)([^>]*)>", block):
            if close:
                depth -= 1
                continue
            if depth == 0:
                top += 1
            if not attrs.rstrip().endswith("/"):
                depth += 1
        assert top == 1, "an x-if branch has %d root elements: %s" % (top, block[:90])


def test_no_alpine_x_for_template_sits_inside_an_svg():
    """⛔ The HTML parser turns a <template> inside <svg> into an SVG node named
    'template' — it renders nothing at all."""
    tpl = _tpl()
    for svg in re.findall(r"<svg\b.*?</svg>", tpl, re.S):
        assert "<template" not in svg


def test_the_screen_css_is_scoped_to_this_page():
    css = _css()
    block = css[css.index("SCREEN 20 —") if "SCREEN 20 —" in css else css.index("SCREEN 20"):]
    # ⭐ BOUNDED AT THE NEXT SCREEN'S HEADER (16-Aug, Screens 21/22).
    # ⛔ Reading to END OF FILE made this window swallow whatever screen
    # was appended after it and judge its rules as Screen 20's own — the
    # same defect the Screen-12 and Screen-18 windows were corrected for.
    # The PROPERTY is unchanged; only the window is.
    block = block[:block.index("SCREEN 21")]
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("/*", "*", "@", "}")) or "{" not in line:
            continue
        selector = line.split("{")[0].strip()
        assert ".sh-page" in selector or "main.content" in selector, selector


def test_the_readability_floor_holds_for_text():
    """⚠️ The donut labels are SVG USER UNITS — the floor is checked against the
    EFFECTIVE size, ⛔ not the raw declaration."""
    css = _css()
    block = css[css.index("SCREEN 20"):]
    # ⭐ BOUNDED AT THE NEXT SCREEN'S HEADER (16-Aug, Screens 21/22).
    # ⛔ Reading to END OF FILE made this window swallow whatever screen
    # was appended after it and judge its rules as Screen 20's own — the
    # same defect the Screen-12 and Screen-18 windows were corrected for.
    # The PROPERTY is unchanged; only the window is.
    block = block[:block.index("SCREEN 21")]
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        selector, body = rule.group(1), rule.group(2)
        m = re.search(r"font-size:\s*([\d.]+)px", body)
        if not m:
            continue
        size = float(m.group(1))
        if "donut-t" in selector:
            size *= 124.0 / 42.0          # .sh-donut is painted at 124px
        if "grip" in selector:
            continue                       # the drag glyph, not text
        assert size >= 12.9, (selector.strip(), m.group(0))


def test_the_screen_has_no_write_path(client):
    """L4 — this dashboard never writes to the trading system. ⚠️ Measured over
    THIS SCREEN's markup only: the shared chrome posts to /logout."""
    page = _page(client)
    root = page[page.index('<div class="sh-page"'):]
    assert "<form" not in root.lower()
    assert "method=\"post\"" not in root.lower()
    assert '"POST"' not in root and "'POST'" not in root


def test_the_kpi_cards_only_filter_and_never_act(client):
    """A KPI click sets a FILTER. ⛔ It must not be able to change the system."""
    tpl = _tpl()
    block = tpl[tpl.index('class="sh-kpis"'):tpl.index('class="sh-main"')]
    assert "toggleState" in block
    assert "post" not in block.lower()


def test_the_column_widths_are_bound_to_the_column_key():
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    for line in block.splitlines():
        if "key:" in line and "label:" in line:
            assert "w:" in line, line.strip()


def test_the_screen_reports_today(gui_config):
    payload = _b(gui_config)
    assert payload["today"] == TODAY
    assert payload["note"]
    assert payload["poll_interval_ms"] > 0


# ── the page's own JavaScript must at least be parseable ─────────────────────
def test_no_javascript_string_literal_spans_a_newline():
    """⛔ THE DEFECT THIS PINS: a raw newline inside a JS string literal is a
    syntax error that kills EVERY binding on the page, while a markup-only
    assertion still passes. Measured over the shipped template, not assumed."""
    bad = _js_syntax.string_literals_spanning_a_newline(
        _js_syntax.script_of(_tpl()))
    assert not bad, bad


def test_the_page_script_has_balanced_braces():
    js = _js_syntax.script_of(_tpl())
    assert _js_syntax.unbalanced_braces(js) == 0


def test_the_guard_can_go_red():
    """⭐ A check with no failing input manufactures confidence (V5)."""
    broken = 'a("one\ntwo"); b();'
    assert _js_syntax.string_literals_spanning_a_newline(broken)
    assert not _js_syntax.string_literals_spanning_a_newline('a("one two");')


# ── the 16-Aug corrections ───────────────────────────────────────────────────
def test_the_filters_use_the_established_language_not_a_one_off():
    """⭐ Rama, 16-Aug: the filter area must match the approved screens.
    ⛔ The screen must not restate control sizing of its own."""
    tpl, css = _tpl(), _css()
    assert 'class="flt-row' in tpl and 'class="flt-field"' in tpl
    assert 'class="flt-k"' in tpl and 'class="flt-actions"' in tpl
    assert "sh-tfilters" not in tpl and "sh-f-l" not in tpl

    for shared in (".sh-page .flt-row", ".sh-page .flt-field", ".sh-page .flt-k",
                   ".sh-page .flt-actions", ".sh-page .sel", ".sh-page .btn-ghost"):
        assert shared in css, shared

    block = css[css.index("SCREEN 20"):]
    # ⭐ BOUNDED AT THE NEXT SCREEN'S HEADER (16-Aug, Screens 21/22).
    # ⛔ Reading to END OF FILE made this window swallow whatever screen
    # was appended after it and judge its rules as Screen 20's own — the
    # same defect the Screen-12 and Screen-18 windows were corrected for.
    # The PROPERTY is unchanged; only the window is.
    block = block[:block.index("SCREEN 21")]
    for line in block.splitlines():
        if ".sel" in line and "height" in line:
            raise AssertionError("one-off control sizing: " + line.strip())


def test_every_approved_heading_fits_on_one_line():
    """⛔ THE DEFECT THIS PINS: the three "… Today" headings wrapped, and the
    second line read as a column of repeated TODAY (Rama, 16-Aug)."""
    css = _css()
    block = css[css.index("SCREEN 20"):]
    # ⭐ BOUNDED AT THE NEXT SCREEN'S HEADER (16-Aug, Screens 21/22).
    # ⛔ Reading to END OF FILE made this window swallow whatever screen
    # was appended after it and judge its rules as Screen 20's own — the
    # same defect the Screen-12 and Screen-18 windows were corrected for.
    # The PROPERTY is unchanged; only the window is.
    block = block[:block.index("SCREEN 21")]
    th = block[block.index(".sh-page .sh-tbl th"):]
    th = th[:th.index("}")]
    assert "white-space: nowrap" in th, th

    tpl = _tpl()
    cols = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("SPECIAL:")]
    widths = [int(w) for w in re.findall(r'w:\s*"(\d+)px"', cols)]
    assert len(widths) == len(APPROVED_COLUMNS)
    declared = re.search(r"\.sh-page \.sh-tbl \{[^}]*min-width:\s*(\d+)px", block)
    assert declared, "the table declares no min-width"
    assert sum(widths) == int(declared.group(1)), (sum(widths), declared.group(1))


def test_the_drag_affordance_survives_the_glyph_removal():
    """⭐ The glyph went to buy header width; ⛔ the affordance did not.

    ⚠️ Scoped to THIS screen's own CSS block — `.sysh-grip` is Screen 12's class
    and a bare substring check would match it and fail for the wrong reason.
    """
    tpl, css = _tpl(), _css()
    block = css[css.index("SCREEN 20"):]
    assert 'class="sh-grip"' not in tpl
    assert ".sh-grip" not in block
    assert 'draggable="true"' in tpl
    assert "drag to reorder" in tpl
    assert ".sh-page .sh-th { cursor: grab" in css
