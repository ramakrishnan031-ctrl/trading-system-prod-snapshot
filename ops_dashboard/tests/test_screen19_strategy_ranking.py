"""SCREEN 19 — STRATEGY RANKING.

The approved design (`gui/19. Strategy_Ranking.png` + `.txt`) is binding for
STRUCTURE; the DATA is this system's.

⭐ THE CENTRAL PROPERTY OF THIS SCREEN: the NEW **Trade Type** column is the
STRATEGY's own configured intent, read from `config/strategies/<name>.yaml`, and
the Trade Type FILTER binds to the SAME value the column shows. ⛔ It is NOT the
order product (MIS/CNC) — a different quantity that lives on `orders.product`.

⚠️ The shared fixture writes `intent` for three of its five strategies and
DELIBERATELY omits it for a fourth, so every assertion below has a failing input
available: a broken YAML read shows as `None` everywhere, an over-eager default
shows as a Trade Type on the strategy that has none.
"""
from __future__ import annotations

import io
import os
import re

import _js_syntax

import yaml

from backend.services import strategy_meta, strategy_ranking

from conftest import TODAY


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "strategy_ranking.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _page(client) -> str:
    html = client.get("/strategy-ranking").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _s(client, qs=""):
    return client.get("/api/strategy-ranking/screen" + qs).get_json()


# ── the Trade Type binding — the new column and its filter ───────────────────
def test_trade_type_comes_from_the_strategy_yaml_intent(gui_config):
    """⭐ The value on screen must be the value in the file — read here from the
    YAML directly, so a change in the service cannot make this pass."""
    cfg_dir = gui_config["paths"]["config_dir"]
    on_disk = {}
    for name in os.listdir(os.path.join(cfg_dir, "strategies")):
        if not name.endswith(".yaml"):
            continue
        with io.open(os.path.join(cfg_dir, "strategies", name), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        on_disk[name[:-5]] = doc.get("intent")

    assert on_disk, "the fixture wrote no strategy YAML at all"
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    by_name = {r["strategy"]: r for r in payload["rows"]}

    expect = {"INTRADAY": "Intraday", "DELIVERY": "Delivery", None: None}
    for name, intent in on_disk.items():
        assert name in by_name, "%s is missing from the ranking" % name
        assert by_name[name]["trade_type"] == expect[intent], (
            "%s: intent %r rendered as %r" % (name, intent, by_name[name]["trade_type"]))


def test_the_fixture_actually_exercises_both_intents_and_an_absent_one(gui_config):
    """⛔ A binding test over a fixture with one intent value proves nothing."""
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    seen = {r["trade_type"] for r in payload["rows"]}
    assert "Intraday" in seen and "Delivery" in seen and None in seen, seen


def test_the_intent_enum_is_the_production_validator_s_enum():
    """⛔ POSITIONAL is not the enum — `strategies/schema.py::_val_intent`
    permits exactly INTRADAY or DELIVERY, and this screen reads that field."""
    assert strategy_meta.YAML_INTENTS == ("INTRADAY", "DELIVERY")
    assert strategy_meta.TRADE_TYPES == ("Intraday", "Delivery")


def test_there_is_exactly_one_trade_type_normaliser():
    """⭐ Screens 19 and 20 must not be able to disagree about one strategy."""
    from backend.services import strategy_tower
    assert strategy_meta.trade_type_of_intent("INTRADAY") == "Intraday"
    assert strategy_meta.trade_type_of_intent("DELIVERY") == "Delivery"
    assert strategy_meta.trade_type_of_intent(None) is None
    # the shared helper IS the tower's own, not a copy of it
    assert strategy_meta._trade_type_of_intent is strategy_tower._trade_type


def test_trade_type_filter_selects_which_strategies_are_listed(gui_config):
    """⭐ Trade Type is a property of the STRATEGY, so it filters the LIST.
    ⛔ It is not a trade attribute and must not merely empty the numbers."""
    all_rows = strategy_ranking.build_strategy_ranking_screen(gui_config)["rows"]
    assert len(all_rows) > 1

    for want in ("Intraday", "Delivery"):
        got = strategy_ranking.build_strategy_ranking_screen(
            gui_config, trade_type=want)["rows"]
        assert got, "no rows for trade_type=%s" % want
        assert {r["trade_type"] for r in got} == {want}
        assert len(got) < len(all_rows), "the filter selected nothing away"


def test_direction_filters_trades_but_keeps_every_strategy_listed(gui_config):
    """⚠️ Direction is a property of the TRADE, not of the strategy — the
    distinction the Trade Type filter turns on."""
    base = strategy_ranking.build_strategy_ranking_screen(gui_config)["rows"]
    got = strategy_ranking.build_strategy_ranking_screen(
        gui_config, direction="LONG")["rows"]
    assert len(got) == len(base)


def test_the_filter_and_the_column_read_the_same_value(gui_config):
    """⛔ A filter that reads a different field from the column it names is a
    screen that contradicts itself."""
    for want in ("Intraday", "Delivery"):
        rows = strategy_ranking.build_strategy_ranking_screen(
            gui_config, trade_type=want)["rows"]
        for r in rows:
            assert r["trade_type"] == want


def test_offered_trade_type_options_are_only_the_ones_present(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    offered = payload["filters"]["trade_type"]
    present = {r["trade_type"] for r in payload["rows"] if r["trade_type"]}
    assert set(offered) == present
    assert offered == sorted(offered, key=strategy_meta.TRADE_TYPES.index)


# ── the approved columns ─────────────────────────────────────────────────────
APPROVED_COLUMNS = ["Rank", "Strategy", "Trade Type", "Trades", "Wins", "Losses",
                    "Win %", "ROI %", "Profit Factor", "Gross P&L", "Net P&L",
                    "Avg Trade", "Best Trade", "Worst Trade", "Score", "Trend"]


def test_the_table_columns_are_the_approved_ones_in_the_approved_order():
    """⭐ Trade Type sits IMMEDIATELY AFTER Strategy, and no approved column
    moved to make room for it."""
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("cols: [], dragKey")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert labels == APPROVED_COLUMNS
    assert labels[1:3] == ["Strategy", "Trade Type"]


def test_the_export_header_matches_the_table_columns():
    assert list(strategy_ranking.EXPORT_HEADER) == APPROVED_COLUMNS


def test_the_export_writes_the_same_rows_the_table_shows(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(
        gui_config, trade_type="Delivery")
    rows = strategy_ranking.export_rows(payload)
    assert rows[0] == list(strategy_ranking.EXPORT_HEADER)
    assert len(rows) - 1 == len(payload["rows"])
    # the Trade Type cell is the third, and it is the strategy's own
    for line, r in zip(rows[1:], payload["rows"]):
        assert line[2] == r["trade_type"]


def test_an_unmeasured_export_cell_is_never_blank(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    rows = strategy_ranking.export_rows(payload)
    for line in rows[1:]:
        for cell in line:
            assert cell != "" and cell is not None


def test_there_is_no_scanner_anywhere_on_this_screen(client):
    """⛔ Rama's terminology ruling: Strategy is the identity, not Scanner."""
    page = _page(client)
    root = page[page.index('<div class="sr-page"'):]
    visible = root[:root.find("<script>")] if "<script>" in root else root
    assert "scanner" not in visible.lower()
    assert "Scanner" not in str(strategy_ranking.EXPORT_HEADER)


# ── the score ────────────────────────────────────────────────────────────────
def test_the_score_weights_are_the_artwork_s_and_total_100():
    weights = {k: w for k, _l, w in strategy_ranking.SCORE_WEIGHTS}
    assert weights == {"profitability": 35, "consistency": 25,
                       "risk_adjusted": 20, "activity": 20}
    assert sum(weights.values()) == 100


def test_a_strategy_with_no_trades_scores_none_not_zero(gui_config):
    """⛔ 0/100 is a measurement; an absence is not."""
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    idle = [r for r in payload["rows"] if r["trades"] == 0]
    assert idle, "the fixture has no idle strategy, so this cannot fail"
    for r in idle:
        assert r["score"] is None
        assert r["trend"] is None


def test_every_score_is_within_its_own_bounds(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    for r in payload["rows"]:
        if r["score"] is None:
            continue
        assert 0 <= r["score"] <= 100
        for part in r["score_parts"].values():
            if part is not None:
                assert 0.0 <= part <= 100.0


def test_the_score_is_reproducible_from_its_published_parts(gui_config):
    """⭐ The parts on the row must ACTUALLY compose the score — otherwise the
    breakdown panel is decoration."""
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    for r in payload["rows"]:
        if r["score"] is None:
            continue
        total = 0.0
        for key, _label, weight in strategy_ranking.SCORE_WEIGHTS:
            v = r["score_parts"].get(key)
            total += (float(v) * weight / 100.0) if v is not None else 0.0
        assert r["score"] == int(round(total))


# ── the trend ────────────────────────────────────────────────────────────────
def test_the_trend_compares_two_equal_windows(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config,
                                                             period="week")
    cur = (payload["from"], payload["to"])
    prev = (payload["prev_from"], payload["prev_to"])
    import datetime as dt
    span = lambda a, b: (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    assert span(*cur) == span(*prev)
    assert prev[1] < cur[0], "the comparison window overlaps the current one"


def test_trend_states_are_only_the_approved_three_or_absent(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    for r in payload["rows"]:
        assert r["trend"] in (None,) + strategy_ranking.TRENDS


def test_the_trend_summary_counts_the_rows_it_is_shown_beside(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    summary = payload["trend_summary"]
    for t in strategy_ranking.TRENDS:
        assert summary[t] == sum(1 for r in payload["rows"] if r["trend"] == t)
    assert summary["unavailable"] == sum(
        1 for r in payload["rows"] if r["trend"] is None)


# ── ranking modes, KPI and the panels ────────────────────────────────────────
def test_the_five_approved_ranking_modes_exist_and_each_one_sorts(gui_config):
    keys = [k for k, _l in strategy_ranking.MODES]
    assert keys == ["net_pnl", "roi", "win_rate", "profit_factor", "trade_count"]
    for key in keys:
        rows = strategy_ranking.build_strategy_ranking_screen(
            gui_config, mode=key)["rows"]
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))


def test_rank_1_actually_leads_the_selected_mode(gui_config):
    for mode, field in (("net_pnl", "net"), ("roi", "roi_pct"),
                        ("win_rate", "win_rate"), ("profit_factor", "profit_factor"),
                        ("trade_count", "trades")):
        rows = strategy_ranking.build_strategy_ranking_screen(
            gui_config, mode=mode)["rows"]
        vals = [r[field] for r in rows if r[field] is not None]
        if len(vals) < 2:
            continue
        assert vals[0] == max(vals), "%s: rank 1 is not the leader" % mode


def test_the_six_approved_kpi_cards_are_present_in_order():
    tpl = _tpl()
    block = tpl[tpl.index("KPIS: ["):tpl.index("TRENDS: [")]
    labels = re.findall(r'label:\s*"([^"]+)"', block)
    assert labels == ["BEST STRATEGY", "WORST STRATEGY", "HIGHEST ROI",
                      "HIGHEST WIN RATE", "HIGHEST PROFIT FACTOR", "MOST TRADES"]


def test_kpi_cards_name_a_real_row_or_nothing(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    names = {r["strategy"] for r in payload["rows"]}
    for key, card in payload["kpi"].items():
        if card is None:
            continue
        assert card["strategy"] in names, key


def test_best_and_worst_are_the_extremes_of_net_pnl(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    traded = [r for r in payload["rows"] if r["trades"]]
    if len(traded) < 2:
        return
    assert payload["kpi"]["best"]["value"] == max(r["net"] for r in traded)
    assert payload["kpi"]["worst"]["value"] == min(r["net"] for r in traded)


def test_winners_and_losers_are_actually_positive_and_negative(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    wl = payload["winners_losers"]
    assert all(w["net"] > 0 for w in wl["winners"])
    assert all(l["net"] < 0 for l in wl["losers"])
    assert len(wl["winners"]) <= 3 and len(wl["losers"]) <= 3


def test_insights_agree_in_number_with_their_own_count(gui_config):
    """⛔ "1 strategies" reads as a formatting bug and puts the number itself in
    doubt."""
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    for line in payload["insights"]:
        m = re.match(r"^(\d+) strateg(y|ies)\b", line["text"])
        if not m:
            continue
        assert (m.group(2) == "y") == (m.group(1) == "1"), line["text"]


def test_the_empty_window_produces_no_invented_numbers(gui_config):
    """A day with no completed trades must show absence, ⛔ not zeroes that
    read as measurements."""
    payload = strategy_ranking.build_strategy_ranking_screen(
        gui_config, period="custom", from_date="1999-01-01", to_date="1999-01-02")
    assert payload["count"] == len(payload["rows"])
    for r in payload["rows"]:
        assert r["trades"] == 0
        assert r["score"] is None and r["trend"] is None
        assert r["win_rate"] is None and r["roi_pct"] is None
    assert payload["winners_losers"]["winners"] == []
    assert payload["kpi"]["best"] is None


def test_percentages_carry_their_base_in_the_payload(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    assert payload["note"]
    assert "relative" in payload["note"].lower()


# ── the shipped page ─────────────────────────────────────────────────────────
def test_the_page_and_both_endpoints_are_reachable(client):
    assert client.get("/strategy-ranking").status_code == 200
    assert client.get("/api/strategy-ranking/screen").status_code == 200
    r = client.get("/api/export/strategy-ranking")
    assert r.status_code == 200
    assert len(r.data) > 1000


def test_the_legacy_ranking_endpoint_still_answers(client):
    """⛔ A new screen must not take an existing contract down with it."""
    assert client.get("/api/strategy-ranking").status_code == 200


def test_the_approved_panels_are_all_on_the_page(client):
    page = _page(client)
    for title in ("STRATEGY RANKING", "RANKING MODE", "FILTERS",
                  "STRATEGY RANKING TABLE", "STRATEGY SCORE BREAKDOWN",
                  "TREND SUMMARY", "RECENT WINNERS / LOSERS", "RANKING INSIGHTS",
                  "Export XLSX"):
        assert title in page, title


def test_the_footer_states_the_completed_trades_basis(client):
    assert "completed trades only" in _page(client)


def test_no_alpine_x_if_branch_ships_two_root_elements():
    """⛔ An x-if template with two siblings renders only the FIRST — which is
    how every "no value" cell came to render blank."""
    tpl = _tpl()
    for block in re.findall(r'<template x-if="[^"]+">(.*?)</template>', tpl, re.S):
        roots = re.findall(r"^\s*<(\w+)", block, re.M)
        depth, top = 0, 0
        for tok in re.findall(r"<(/?)(\w+)([^>]*)>", block):
            close, tag, attrs = tok
            if close:
                depth -= 1
                continue
            if depth == 0:
                top += 1
            if not attrs.rstrip().endswith("/"):
                depth += 1
        assert top == 1, "an x-if branch has %d root elements: %s" % (top, block[:90])


def test_the_screen_css_is_scoped_to_this_page():
    css = _css()
    block = css[css.index("SCREEN 19 - STRATEGY RANKING")
                if "SCREEN 19 - STRATEGY RANKING" in css
                else css.index("SCREEN 19"):]
    block = block[:block.index("SCREEN 20")]
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("/*", "*", "@", "}")) or "{" not in line:
            continue
        selector = line.split("{")[0].strip()
        assert ".sr-page" in selector or "main.content" in selector, selector


def test_the_readability_floor_holds_for_text():
    """⚠️ The donut labels are SVG USER UNITS, not px — their declared number is
    scaled by (svg width / viewBox width), so the floor is checked against the
    EFFECTIVE size, ⛔ not against the raw declaration."""
    css = _css()
    block = css[css.index("SCREEN 19"):]
    block = block[:block.index("SCREEN 20")]
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        selector, body = rule.group(1), rule.group(2)
        m = re.search(r"font-size:\s*([\d.]+)px", body)
        if not m:
            continue
        size = float(m.group(1))
        if "donut-t" in selector:
            size *= 116.0 / 42.0          # .sr-donut is painted at 116px
        if "grip" in selector:
            continue                       # the drag glyph, not text
        assert size >= 12.9, (selector.strip(), m.group(0))


def test_the_screen_has_no_write_path(client):
    """L4 — this dashboard never writes to the trading system. ⚠️ Measured over
    THIS SCREEN's markup only: the shared chrome posts to /logout, and counting
    that would make the check pass or fail for the wrong reason."""
    page = _page(client)
    root = page[page.index('<div class="sr-page"'):]
    assert "<form" not in root.lower()
    assert "method=\"post\"" not in root.lower()
    assert "method: \"post\"" not in root.lower()
    assert '"POST"' not in root and "'POST'" not in root


def test_the_column_widths_are_bound_to_the_column_key():
    """⛔ Widths bound to a POSITION get reassigned by a drag."""
    tpl = _tpl()
    block = tpl[tpl.index("DEFAULT_COLS:"):tpl.index("cols: [], dragKey")]
    for line in block.splitlines():
        if "key:" in line and "label:" in line:
            assert "w:" in line, line.strip()


def test_today_is_the_default_window(gui_config):
    payload = strategy_ranking.build_strategy_ranking_screen(gui_config)
    assert payload["from"] == payload["to"] == TODAY


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
    """⭐ Rama, 16-Aug: the filter controls must match the approved screens, and
    the panel must not leave a large empty area on its right."""
    tpl, css = _tpl(), _css()
    assert 'class="flt-row"' in tpl and 'class="flt-field"' in tpl
    assert 'class="flt-k"' in tpl and 'class="flt-actions"' in tpl
    assert "sr-frow" not in tpl and "sr-f-l" not in tpl and "sr-fbtns" not in tpl

    for shared in (".sr-page .flt-row", ".sr-page .flt-field", ".sr-page .flt-k",
                   ".sr-page .flt-actions", ".sr-page .sel", ".sr-page .btn-ghost"):
        assert shared in css, shared

    # ⭐ the shared field GROWS (flex: 1 1 150px) — that is what fills the panel
    field = css[css.index(".sr-page .flt-field"):]
    field = field[:field.index("}")]
    assert "flex: 1 1 150px" in field, field

    block = css[css.index("SCREEN 19"):css.index("SCREEN 20")]
    for line in block.splitlines():
        if ".sel" in line and "height" in line:
            raise AssertionError("one-off control sizing: " + line.strip())


def test_the_trade_type_filter_is_an_ordinary_dropdown():
    """⭐ Required immediately after Strategy in the TABLE, but its FILTER stays a
    normal select like every other filter on the page."""
    tpl = _tpl()
    i = tpl.index('<span class="flt-k">Trade Type</span>')
    block = tpl[i:i + 400]
    assert '<select class="sel"' in block, block[:200]


def test_the_drag_affordance_survives_the_glyph_removal():
    """⚠️ Scoped to THIS screen's own CSS block — a bare substring check would
    match another screen's class and fail for the wrong reason."""
    tpl, css = _tpl(), _css()
    block = css[css.index("SCREEN 19"):css.index("SCREEN 20")]
    assert 'class="sr-grip"' not in tpl
    assert ".sr-grip" not in block
    assert 'draggable="true"' in tpl and "drag to reorder" in tpl
    assert ".sr-page .sr-th { cursor: grab" in css
