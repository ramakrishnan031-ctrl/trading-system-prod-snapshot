"""Screen 10 — Slippage Analytics.

Covers the three rulings the screen had to apply to the reference design
(Scanner removed · "Signal Score" not resurrected · Screen-08/09 alignment by
column role) and the data-integrity rules it must not break: one filtered
population behind every panel, a status partition that always adds up, an
UNMEASURED row that can never pass as a clean fill, the per-trade tolerance the
order path actually applied, five price buckets that always render, and an
export that cannot disagree with the table.

⭐ Assertions are anchored to the fixture's KNOWN values or to a PROPERTY that
must hold. ⛔ No assertion is written by reading back what the code happened to
produce.

FIXTURE ARITHMETIC (conftest), so every number below is traceable:
  TODAY   trd_c1  AAA @1000  slip 3.00  dist 10  frac 0.15 (symbol:AAA)
                  ⇒ allowed min(10×0.15, 5) = 1.50 ⇒ 200%  → EXCEEDED
  TODAY   trd_c4  AAA @1000  slip 0.50  dist 10  frac —    (config-global 0.22)
                  ⇒ allowed min(10×0.22, 5) = 2.20 ⇒  23%  → WITHIN_LIMIT
  YDAY    trd_w1  BBB @  85  slip 0.30  dist  5              ⇒  27%  → WITHIN_LIMIT
  YDAY    trd_w2  CCC @ 450  slip 1.55  dist 10              ⇒  70%  → NEAR_LIMIT
  10D AGO trd_m1  DDD @ 150  slip NULL                       →  UNMEASURED
"""
from __future__ import annotations

import os
import re

from backend.services import slippage_analytics as sa

from conftest import TENDAYS, TODAY, YDAY  # noqa: E402


def _read(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tpl() -> str:
    return _read("frontend", "templates", "slippage.html")


def _css() -> str:
    return _read("frontend", "static", "style.css")


def _css_rules() -> list:
    """(selector, body) for every rule in the Screen-10 block, with /* … */
    comments stripped FIRST.

    ⚠️ Written after the first version scanned raw lines and tripped over its own
    documentation — a comment line naming `.curve-wrap { … }` is prose, not a
    rule. A check that fails on prose would have to be loosened until it stopped
    checking anything."""
    block = _css().split("SCREEN 10 — SLIPPAGE ANALYTICS", 1)[1]
    # ⚠️ The split lands INSIDE the block's own opening /* … */, so the comment
    # stripper below would never see that opener. Drop to the first close first.
    block = block.split("*/", 1)[1]
    block = re.sub(r"/\*.*?\*/", " ", block, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", block)


def _shipped(client) -> str:
    """The VISIBLE HTML the browser receives: rendered, then with `<!-- … -->`
    comments removed.

    Two different things get stripped for two different reasons. Jinja `{# … #}`
    notes never leave the server at all. HTML comments DO ship (house style —
    Screens 06-09 carry their design notes that way), but they are not UI: a note
    that says "Scanner Slippage Ranking removed" must not be readable as the
    ranking being present. Both tripped the first version of these tests."""
    html = client.get("/slippage").data.decode("utf-8")
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _month(client, extra=""):
    return client.get("/api/analytics/slippage?period=month" + extra).get_json()


# ── RULING 1: SCANNER IS GONE ────────────────────────────────────────────────
def test_scanner_absent_from_the_payload(client):
    """No scanner dimension at all — filter, option list, row key or ranking.
    Strategy IS the scanner identity on this system."""
    d = _month(client)
    assert "per_scanner" not in d
    assert "scanner" not in (d["filters"]["options"] or {})
    assert "scanner" not in d["filters"]["keys"]
    for row in d["rows"]:
        assert "scanner" not in row


def test_scanner_absent_from_the_screen(client):
    """Belt and braces: a payload without it and a page that still showed an
    empty Scanner column would look like a bug to the operator. Asserted against
    the SHIPPED HTML, so the design note explaining the removal cannot itself
    trip the check."""
    html = _shipped(client)
    assert "All Scanners" not in html               # the filter's "all" option
    assert "Scanner Slippage Ranking" not in html   # the ranking panel
    assert 'label:"Scanner"' not in html            # a table column
    assert "scanner" not in _month(client)["filters"]["options"]
    # The ONE surviving mention is the note that says WHY it is absent — the same
    # note Screen 09 carries. ⛔ Anything else would be a leftover.
    body = html.split("slp-page", 1)[1]
    assert body.count("Scanner") == 1
    assert "Scanner ranking omitted" in body


def test_scanner_filter_is_not_silently_accepted(client):
    """Passing ?scanner= must not narrow anything — there is no such dimension.
    ⛔ A filter that is quietly ignored would be worse than one that errors."""
    a = _month(client)
    b = _month(client, "&scanner=gap_fade_long")
    assert a["order_count"] == b["order_count"]


def test_scanner_attribution_screen_is_untouched(client):
    """⛔ Removing scanner from Screen 10 must NOT remove it from the Scanner
    Attribution screen, which is a different question with its own endpoint."""
    assert client.get("/api/scanner-attribution?period=week").get_json()["rows"]


def test_existing_today_scoped_slippage_endpoint_is_untouched(client):
    """The G2b-2 `/api/slippage` contract keeps its own shape, scanner and all —
    Screen 10 is ADDITIVE, exactly as /api/analytics/pnl was to /api/pnl."""
    d = client.get("/api/slippage").get_json()
    assert d["count"] == 2
    assert d["per_scanner"], "the old endpoint keeps its scanner rollup"


# ── RULING 2: "SIGNAL SCORE" IS NOT RESURRECTED ──────────────────────────────
def test_score_columns_follow_the_13_aug_ruling(client):
    """The reference design asks for "Signal Score"; that label was retired
    system-wide on 13-Aug. The screen carries System Score (ACHIEVED) + Score
    Threshold (the minimum), and the two must be DIFFERENT numbers here — with
    one value for both, a reader that returned the threshold under the score's
    name would pass anyway."""
    html = _shipped(client)
    assert "Signal Score" not in html
    assert "System Score" in html and "Score Threshold" in html

    d = _month(client)
    scored = [r for r in d["rows"] if r["system_score"] is not None]
    assert scored, "fixture links two trades to screener rows"
    for r in scored:
        assert r["system_score"] == 72          # screener_results.score  (ACHIEVED)
        assert r["score_threshold"] == 65       # eligible_score          (THRESHOLD)
    assert "signal_score" not in d["rows"][0]


# ── RULING 3: alignment BY COLUMN ROLE (Screens 08/09) ───────────────────────
def test_alignment_is_by_column_role_not_blanket():
    css = _css()
    assert ".slp-page .cap-table th { text-align: center; }" in css
    assert ".slp-page .cap-table th.lbl { text-align: left; }" in css
    # ⛔ never a blanket rule over every cell
    assert ".slp-page table td { text-align: center" not in css
    assert ".slp-page .cap-table td { text-align: center" not in css


def test_label_columns_are_declared_left_and_data_columns_centred():
    """The role is stated in the markup — ⛔ not guessed positionally. Strategy
    is the THIRD heading, so `th:first-child` could not have expressed it."""
    t = _tpl()
    cols = re.search(r"COLS:\s*\[(.*?)\n\s*\],", t, re.S).group(1)
    entries = re.findall(r'\{\s*key:"(\w+)",\s*label:"([^"]+)"(.*?)\}', cols, re.S)
    labels = [lbl for _k, lbl, _rest in entries]
    assert labels[:6] == ["Trading Date", "Time", "Strategy", "Symbol",
                          "Trade Type", "Direction"]
    assert "Scanner" not in labels
    # Exactly the two row-label columns are LEFT; every other heading is data.
    left = [lbl for _k, lbl, rest in entries if "lbl:true" in rest]
    assert left == ["Trading Date", "Strategy"]
    # and the body marks its data cells with the codebase's own marker
    assert 'class="ctr"' in t


def test_symbol_is_not_duplicated():
    """The reference design lists Symbol in the common column standard AND again
    at the head of the slippage block. It appears ONCE."""
    labels = re.findall(r'label:"([^"]+)"', _tpl())
    assert labels.count("Symbol") == 1


def test_approved_slippage_columns_are_all_present():
    labels = re.findall(r'label:"([^"]+)"', _tpl())
    for want in ("Entry Price (System)", "Entry Price (Filled)", "Allowed Slippage",
                 "Actual Slippage", "Slippage %", "Status"):
        assert want in labels, want


# ── The tolerance the ORDER PATH actually applied ────────────────────────────
def test_allowed_slippage_uses_the_trades_own_resolved_fraction(client):
    """trd_c1 carries tolerance_fraction_used=0.15 (a by_symbol override) and
    trd_c4 carries none. A reader that ignored the persisted value and always
    used the config global would give BOTH 2.20 and fail here."""
    rows = {r["trade_id"]: r for r in _month(client)["rows"]}
    assert rows["trd_c1"]["allowed_slippage_rs"] == 1.50      # min(10 × 0.15, 5)
    assert rows["trd_c1"]["tolerance_source"] == "symbol:AAA"
    assert rows["trd_c4"]["allowed_slippage_rs"] == 2.20      # min(10 × 0.22, 5)
    assert rows["trd_c4"]["tolerance_source"] == "config-global"


def test_allowed_slippage_is_capped_by_the_hard_ceiling():
    """A PROPERTY of the model, checked directly: no configuration of fraction
    and SL distance may produce a tolerance above hard_max_slippage_rs."""
    model = {"mode": "sl_fraction", "max_slippage_fraction": 0.9,
             "absolute_cap_rs": 500.0, "hard_max_slippage_rs": 10.0}
    allowed, _frac, _src = sa.allowed_slippage(
        {"planned_sl_distance": 10_000.0}, model)
    assert allowed == 10.0


def test_missing_sl_distance_falls_back_to_the_cap_not_to_none():
    """The order path uses the absolute cap when the SL price is unavailable, so
    the screen must too — ⛔ a row is never left with no tolerance and therefore
    silently unjudged when the system did judge it."""
    model = {"mode": "sl_fraction", "max_slippage_fraction": 0.22,
             "absolute_cap_rs": 5.0, "hard_max_slippage_rs": 10.0}
    allowed, _f, _s = sa.allowed_slippage({"planned_sl_distance": None}, model)
    assert allowed == 5.0


# ── STATUS: the partition, and the honest fourth state ───────────────────────
def test_status_thresholds_are_shares_of_the_allowed_slippage():
    """BASE = the allowed slippage, ⛔ not the price. Boundaries checked on both
    sides, including the exact 60% and 100% edges."""
    assert sa.classify(0.59, 1.0)[0] == "WITHIN_LIMIT"
    assert sa.classify(0.60, 1.0)[0] == "NEAR_LIMIT"      # inclusive floor
    assert sa.classify(1.00, 1.0)[0] == "NEAR_LIMIT"      # 100% is not yet a breach
    assert sa.classify(1.01, 1.0)[0] == "EXCEEDED"
    assert sa.classify(-0.5, 1.0)[0] == "WITHIN_LIMIT"    # a FAVOURABLE fill


def test_an_unmeasurable_row_is_never_counted_as_a_pass():
    """⛔ The failure this guards: a broken recorder writing NULL slippage would
    otherwise make a dead day look like a perfect one."""
    assert sa.classify(None, 2.0) == ("UNMEASURED", None)
    assert sa.classify(1.0, None) == ("UNMEASURED", None)
    assert sa.classify(1.0, 0.0) == ("UNMEASURED", None)


def test_the_four_kpi_counts_partition_the_population(client):
    """A PROPERTY, ⛔ not a magic number: the KPI deck must always add up, on
    every period and under every filter."""
    for q in ("period=today", "period=week", "period=month",
              "period=month&direction=LONG", "period=month&status=EXCEEDED"):
        t = client.get("/api/analytics/slippage?" + q).get_json()["totals"]
        assert (t["within_limit"] + t["near_limit"] + t["exceeded"]
                + t["unmeasured"]) == t["total_orders"], q


def test_fixture_statuses_are_exactly_the_arithmetic(client):
    rows = {r["trade_id"]: r for r in _month(client)["rows"]}
    assert rows["trd_c1"]["status"] == "EXCEEDED" and rows["trd_c1"]["status_ratio_pct"] == 200.0
    assert rows["trd_c4"]["status"] == "WITHIN_LIMIT"
    assert rows["trd_w1"]["status"] == "WITHIN_LIMIT"
    assert rows["trd_w2"]["status"] == "NEAR_LIMIT"
    assert rows["trd_m1"]["status"] == "UNMEASURED"


def test_average_is_over_measured_rows_only(client):
    """⛔ An absent slippage must not be averaged in as 0.00 — it would drag the
    mean toward zero and make execution look better than it was measured to be."""
    t = _month(client)["totals"]
    assert t["measured"] == 4 and t["total_orders"] == 5
    assert t["avg_slippage_rs"] == round((3.0 + 0.5 + 0.3 + 1.55) / 4, 4)
    assert t["worst_slippage_rs"] == 3.0


# ── ONE filtered population behind every panel ───────────────────────────────
def test_every_panel_describes_the_same_filtered_set(client):
    d = _month(client)
    assert len(d["rows"]) == d["order_count"] == d["totals"]["total_orders"]
    assert sum(b["orders"] for b in d["price_buckets"]) == len(d["rows"])
    assert sum(r["orders"] for r in d["per_strategy"]) == len(d["rows"])
    assert sum(r["orders"] for r in d["per_symbol"]) == len(d["rows"])
    assert sum(s["count"] for s in d["status_distribution"]) == len(d["rows"])
    assert d["rr"]["total_trades"] == len(d["rows"])
    assert d["impact"]["total_rows"] == len(d["rows"])


def test_a_filter_narrows_every_panel_together(client):
    d = _month(client, "&status=EXCEEDED")
    assert d["order_count"] == 1 and d["order_count_unfiltered"] == 5
    assert all(r["status"] == "EXCEEDED" for r in d["rows"])
    assert sum(b["orders"] for b in d["price_buckets"]) == 1
    assert sum(s["count"] for s in d["status_distribution"]) == 1
    assert d["rr"]["total_trades"] == 1


def test_price_bucket_filter_uses_the_system_price(client):
    d = _month(client, "&price_bucket=0-100")
    assert [r["symbol"] for r in d["rows"]] == ["BBB"]     # @ 85
    assert d["rows"][0]["price_bucket"] == "0-100"


def test_filter_options_come_from_the_unfiltered_period(client):
    """Choosing one value must not erase the others from the dropdown."""
    d = _month(client, "&symbol=AAA")
    assert d["filters"]["options"]["symbol"] == ["AAA", "BBB", "CCC", "DDD"]
    assert d["order_count"] == 2 and d["order_count_unfiltered"] == 5


# ── PRICE BUCKETS: the five approved, and the honest empty one ───────────────
def test_the_five_approved_buckets_always_render(client):
    d = _month(client)
    assert [b["bucket"] for b in d["price_buckets"]] == [
        "0-100", "100-200", "200-500", "500-1000", "1000+"]


def test_an_empty_bucket_is_unobserved_not_a_measured_zero(client):
    """⛔ ₹0.00 would read as "measured, and it was perfect". A bucket with no
    orders reports observed=False and None."""
    b = {x["bucket"]: x for x in _month(client)["price_buckets"]}
    assert b["500-1000"] == {"bucket": "500-1000", "orders": 0, "measured": 0,
                             "observed": False, "avg_slippage_rs": None,
                             "worst_slippage_rs": None, "exceeded": 0,
                             "exceeded_pct": None}


def test_buckets_are_a_partition_over_their_boundaries():
    """[lo, hi): a price on a boundary belongs to exactly ONE bucket."""
    for px, want in ((0.0, "0-100"), (99.99, "0-100"), (100.0, "100-200"),
                     (199.99, "100-200"), (200.0, "200-500"), (499.99, "200-500"),
                     (500.0, "500-1000"), (999.99, "500-1000"), (1000.0, "1000+"),
                     (99999.0, "1000+")):
        assert sa.resolve_price_bucket(px) == want, px
    assert sa.resolve_price_bucket(None) is None


def test_buckets_are_derived_from_price_not_from_the_stored_band(client):
    """The persisted price_band uses SIX configured bands; the approved screen
    shows FIVE. trd_w2's stored band is "300-500" and it must appear under the
    approved "200-500" — and the payload must SAY the two differ."""
    d = _month(client)
    row = [r for r in d["rows"] if r["trade_id"] == "trd_w2"][0]
    assert row["price_band"] == "300-500"        # what the system stored
    assert row["price_bucket"] == "200-500"      # what the approved screen shows
    assert "price_band" in d["bucket_note"] and "SYSTEM price" in d["bucket_note"]


# ── RR: two quantities, two names ────────────────────────────────────────────
def test_rr_damage_and_rr_degradation_are_reported_separately(client):
    """⛔ One label, one meaning. The persisted rr_damage_pct (base = the planned
    SL distance) and the reference design's RR ratio loss (base = the planned
    R:R) are DIFFERENT numbers and neither may be shown under the other's name."""
    rr = _month(client)["rr"]
    assert rr["rr_damage_pct_avg"] == round((27.0 + 4.0 + 6.0 + 15.5) / 4, 4)
    exp = [(1.5, 1.1), (1.5, 1.45), (2.0, 1.7), (2.0, 1.5)]
    assert rr["rr_degradation_pct_avg"] == round(
        sum(100.0 * (p - a) / p for p, a in exp) / 4, 4)
    assert rr["rr_damage_pct_avg"] != rr["rr_degradation_pct_avg"]
    assert "SL distance" in rr["rr_damage_basis"]
    assert "planned R:R" in rr["rr_degradation_basis"]


def test_rr_means_report_their_own_n(client):
    """A mean over four trades must not be able to look like a mean over 300."""
    rr = _month(client)["rr"]
    assert rr["expected_rr_n"] == 4 and rr["actual_rr_n"] == 4
    assert rr["rr_damage_n"] == 4 and rr["rr_degradation_n"] == 4
    assert rr["total_trades"] == 5      # trd_m1 carries no RR at all


# ── BUSINESS IMPACT ──────────────────────────────────────────────────────────
def test_impact_decomposes_exactly(client):
    """potential = actual + lost, and lost = Σ(slippage × qty) over the rows that
    carry both. Every figure is a sum over real rows."""
    imp = _month(client)["impact"]
    assert imp["pnl_lost_to_slippage"] == round((3.0 + 0.5 + 0.3 + 1.55) * 10, 2)
    assert imp["potential_pnl"] == round(imp["actual_pnl"] + imp["pnl_lost_to_slippage"], 2)
    assert imp["impact_pct"] == round(
        100.0 * imp["pnl_lost_to_slippage"] / abs(imp["potential_pnl"]), 2)
    assert imp["costed_rows"] == 4 and imp["total_rows"] == 5
    assert "entry leg only" in imp["note"]


def test_impact_counts_each_trade_once(client):
    """The P&L side is de-duplicated by trade_id, so a trade cannot contribute
    its net twice if it ever produced two roll-up rows."""
    d = _month(client)
    nets = {r["trade_id"]: r["net_pnl"] for r in d["rows"]}
    assert d["impact"]["actual_pnl"] == round(sum(nets.values()), 2)


# ── TREND ────────────────────────────────────────────────────────────────────
def test_trend_uses_the_approved_timeline_and_entry_time(client):
    """Bucketed by ENTRY time — when the slippage happened, ⛔ not exit time. The
    edges are the approved 09:15 · 10:00 · 11:00 · 12:00 · 13:00 · 14:00 · 15:30."""
    tr = _month(client)["trend"]
    assert [b["at"] for b in tr] == ["09:15", "10:00", "11:00", "12:00", "13:00", "14:00"]
    by = {b["at"]: b for b in tr}
    # trd_c1 + trd_c4 both entered at 10:06 today
    assert by["10:00"]["measured"] == 2
    assert by["10:00"]["avg_slippage_rs"] == round((3.0 + 0.5) / 2, 4)
    assert "ENTRY time" in _month(client)["trend_note"]


def test_an_empty_trend_bucket_is_unobserved(client):
    """The chart must be able to BREAK the line rather than draw through a bucket
    nothing was measured in."""
    by = {b["at"]: b for b in _month(client)["trend"]}
    assert by["09:15"]["observed"] is False
    assert by["09:15"]["avg_slippage_rs"] is None


def test_the_chart_breaks_the_line_across_a_gap():
    """The template must build SEPARATE polyline segments — a single polyline
    would interpolate a measurement that was never taken."""
    t = _tpl()
    assert "trendSegments()" in t
    assert "trendPoints()" in t


def test_no_alpine_template_loop_inside_an_svg():
    """⛔ PINNED, because it cost a blank chart that every test was green for.

    `<template>` written inside `<svg>` is parsed by the HTML parser as an SVG
    element named "template" in the SVG namespace — NOT an HTMLTemplateElement —
    so Alpine finds no `.content` to repeat and renders NOTHING. The panel looked
    like an empty box while the payload was perfect.

    Repeated SVG children must be built as markup and injected with `x-html` on a
    <g>, which is what Screens 06/07/09 already do for their pie arcs. This scans
    EVERY template in the tree, so the trap cannot reappear on another screen."""
    import glob

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for path in glob.glob(os.path.join(here, "frontend", "templates", "*.html")):
        html = open(path, encoding="utf-8").read()
        for svg in re.findall(r"<svg\b.*?</svg>", html, re.S):
            if re.search(r"<template\b", svg):
                offenders.append(os.path.basename(path))
    assert not offenders, (
        "<template x-for> inside <svg> renders nothing — use x-html on a <g>: "
        + ", ".join(sorted(set(offenders))))


# ── EXPORT ───────────────────────────────────────────────────────────────────
def test_export_is_the_filtered_set_and_nothing_else(client):
    """It calls the SAME builder with the SAME arguments, so the sheet and the
    table cannot disagree about what "filtered" means."""
    from openpyxl import load_workbook
    import io

    r = client.get("/api/export/slippage?period=month&status=EXCEEDED")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["Content-Type"]
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Slippage Details"]
    header = [c.value for c in ws[1]]
    assert header[:6] == ["Trading Date", "Time", "Strategy", "Symbol",
                          "Trade Type", "Direction"]
    assert "Scanner" not in header
    assert "Signal Score" not in header
    assert "System Score" in header and "Score Threshold" in header
    body = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(body) == 1                       # only the one EXCEEDED row
    assert body[0][header.index("Status")] == "EXCEEDED"


def test_export_covers_every_panel_a_button_sits_on(client):
    """⛔ An "Export XLSX" button on the Strategy Ranking panel must not hand you
    the detail rows. The workbook carries a sheet for each panel that offers the
    button, all from the SAME filtered payload."""
    from openpyxl import load_workbook
    import io

    wb = load_workbook(io.BytesIO(
        client.get("/api/export/slippage?period=month").data))
    assert wb.sheetnames == ["Slippage Details", "Price Buckets",
                             "Strategy Ranking", "Symbol Ranking", "Summary"]
    d = _month(client)
    assert wb["Slippage Details"].max_row == len(d["rows"]) + 1
    assert wb["Price Buckets"].max_row == 6            # five buckets + header
    assert wb["Strategy Ranking"].max_row == len(d["per_strategy"]) + 1
    assert wb["Symbol Ranking"].max_row == len(d["per_symbol"]) + 1
    for name in ("Strategy Ranking", "Symbol Ranking"):
        assert "Scanner" not in [c.value for c in wb[name][1]]


def test_export_sheets_agree_with_the_screen(client):
    """Every sheet is derived from the ONE payload, so the numbers must match the
    ones the screen shows — ⛔ not merely be 'about right'."""
    from openpyxl import load_workbook
    import io

    d = _month(client, "&status=EXCEEDED")
    wb = load_workbook(io.BytesIO(
        client.get("/api/export/slippage?period=month&status=EXCEEDED").data))
    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(min_row=2, values_only=True)}
    assert summary["Total Orders"] == d["totals"]["total_orders"]
    assert summary["Orders Exceeded Limit"] == d["totals"]["exceeded"]
    assert summary["P&L Lost to Slippage"] == d["impact"]["pnl_lost_to_slippage"]
    assert summary["Filters applied"] == "status=EXCEEDED"
    bkt = {r[0]: r[1] for r in wb["Price Buckets"].iter_rows(min_row=2, values_only=True)}
    for b in d["price_buckets"]:
        assert bkt[b["bucket"]] == b["orders"]


def test_export_filename_carries_the_range(client):
    r = client.get("/api/export/slippage?period=today")
    assert f"slippage_{TODAY}_to_{TODAY}.xlsx" in r.headers["Content-Disposition"]


# ── PERIOD LAYER ─────────────────────────────────────────────────────────────
def test_period_windows_select_the_right_days(client):
    def n(p):
        return client.get("/api/analytics/slippage?period=" + p).get_json()["order_count"]
    assert n("today") == 2                       # trd_c1, trd_c4
    assert n("week") == 4                        # + trd_w1, trd_w2 (YDAY)
    assert n("month") == 5                       # + trd_m1 (10 days ago)


def test_custom_range_is_honoured(client):
    d = client.get(f"/api/analytics/slippage?period=custom&from={YDAY}&to={YDAY}").get_json()
    assert d["from"] == YDAY and d["to"] == YDAY
    assert {r["trade_id"] for r in d["rows"]} == {"trd_w1", "trd_w2"}
    d2 = client.get(
        f"/api/analytics/slippage?period=custom&from={TENDAYS}&to={TENDAYS}").get_json()
    assert {r["trade_id"] for r in d2["rows"]} == {"trd_m1"}


def test_no_default_row_cap(client):
    """⛔ A silent truncation would let the KPI deck, the table and the buckets
    describe different populations with nothing on screen saying so."""
    d = _month(client)
    assert d["row_cap"] is None and d["row_cap_applied"] is False


# ── TRADE TYPE: honest UNKNOWN ───────────────────────────────────────────────
def test_trade_type_is_never_invented(client):
    """There is no trades.product column; product lives on the ENTRY order. A
    trade with no ENTRY order row has NO product and must stay VISIBLE as
    UNKNOWN — ⛔ never defaulted to MIS/CNC, never silently dropped."""
    d = _month(client)
    by = {r["trade_id"]: r["trade_type"] for r in d["rows"]}
    assert by["trd_c1"] == "MIS"                 # has an ENTRY order
    assert by["trd_w1"] == "UNKNOWN"             # has none
    assert "UNKNOWN" in d["filters"]["options"]["trade_type"]
    assert len(_month(client, "&trade_type=UNKNOWN")["rows"]) == 3


# ── THE SCREEN ITSELF ────────────────────────────────────────────────────────
def test_route_renders(client):
    r = client.get("/slippage")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "slp-page" in body and "Slippage Analytics" in body


def test_real_time_refresh_is_preserved():
    """The screen re-fetches on the root component's `ops-refresh` broadcast, and
    the poll path must NOT reset the pager, the sort or the filters — the
    operator is looking at the page while it refreshes."""
    t = _tpl()
    assert '@ops-refresh.window="refresh()"' in t
    body = re.search(r"refresh\(\)\s*\{(.*?)\}", t, re.S).group(1)
    assert "tPage" not in body and "applied" not in body


def test_no_hard_coded_values_in_the_markup():
    """Every KPI, cell and chart point is bound to the payload. ⛔ Nothing on
    this screen may be a literal from the reference PNG."""
    t = _tpl()
    for ghost in ("1,483", "1,102", "₹2.18", "₹32.45", "18,420", "74.28"):
        assert ghost not in t, ghost


def test_status_colours_are_the_approved_three_plus_an_honest_fourth():
    css = _css()
    assert ".slp-page .slp-WITHIN_LIMIT { background: var(--pos-tint); color: var(--pos); }" in css
    assert ".slp-page .slp-NEAR_LIMIT   { background: var(--warn-tint); color: var(--yellow); }" in css
    assert ".slp-page .slp-EXCEEDED     { background: var(--neg-tint); color: var(--neg); }" in css
    # grey, and deliberately NOT one of the three grades
    assert ".slp-page .slp-UNMEASURED   { background: var(--muted-tint); color: var(--dim); }" in css


def test_donut_is_stroked_not_a_filled_disc():
    """The Screen-09 lesson, carried: `.pos-donut circle { fill:none }` is scoped
    to `.pos-page`, so a donut on any other page renders as a solid black disc
    without its own rule. All 66 Screen-09 tests were green while that was
    broken — only the browser caught it."""
    assert ".slp-page .pos-donut circle { fill: none; stroke-width: 5; }" in _css()


def test_css_is_scoped_to_this_screen():
    """⛔ No Screen-10 rule may escape `.slp-page` and re-align or restyle a
    table on a screen nobody asked about."""
    rules = _css_rules()
    assert len(rules) > 40, "the block must actually have been found and parsed"
    for selector, _body in rules:
        selector = selector.strip()
        if selector.startswith("@media"):
            continue
        for part in selector.split(","):
            part = part.strip().lstrip("{").strip()
            if part:
                assert part.startswith(".slp-page"), part


def test_other_screens_are_untouched():
    """Screens 08 and 09 keep their own alignment rules exactly as approved."""
    css = _css()
    assert ".cap-page .cap-table th { text-align: center; }" in css
    assert ".cap-page .cap-table th:first-child { text-align: left; }" in css
    assert ".pnl-page .cap-table th { text-align: center; }" in css
    assert ".pnl-page .cap-table th.lbl { text-align: left; }" in css
    assert "table td.ctr { text-align: center !important; }" in css
