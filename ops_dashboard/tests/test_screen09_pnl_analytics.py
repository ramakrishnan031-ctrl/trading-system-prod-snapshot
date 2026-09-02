"""Screen 09 — P&L Analytics.

Covers the three approved overrides (Scanner removed · Time column · Screen-08
alignment by column role) and the data-integrity rules the screen must not break:
one filtered population behind every panel, realized-only equity (G4), honest
UNKNOWN trade type, honest unavailable drawdown, and an attribution that cannot
double-count.

⭐ Assertions are anchored to the fixture's KNOWN values or to a PROPERTY that
must hold (totals == Σ rows). ⛔ No assertion is written by reading back what the
code happened to produce.
"""
from __future__ import annotations

import re

from backend.services import analytics_period as ap

from conftest import TODAY, YDAY  # noqa: E402

_TPL = "ops_dashboard/frontend/templates/pnl_analytics.html"
_CSS = "ops_dashboard/frontend/static/style.css"


def _tpl(frontend_root=None) -> str:
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "frontend", "templates", "pnl_analytics.html"),
              encoding="utf-8") as fh:
        return fh.read()


def _css() -> str:
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "frontend", "static", "style.css"), encoding="utf-8") as fh:
        return fh.read()


# ── Override 1: SCANNER IS GONE ──────────────────────────────────────────────
def test_scanner_absent_from_the_payload(client):
    """The endpoint must not carry a scanner dimension at all — column, ranking
    or attribution. Strategy IS the scanner identity on this system."""
    d = client.get("/api/analytics/pnl?period=week").get_json()
    assert "per_scanner" not in d
    assert "scanner" not in (d.get("filters", {}).get("options") or {})
    assert "scanner" not in (d.get("filters", {}).get("keys") or [])
    for row in d["rows"]:
        assert "scanner" not in row
    assert d["best_worst"].get("best_scanner") is None
    assert d["best_worst"].get("worst_scanner") is None


def test_scanner_absent_from_the_screen():
    """Belt and braces: the rendered template must not mention a Scanner column,
    filter or ranking either. ⛔ A payload without it and a template that still
    shows an empty column would look like a bug to the operator."""
    t = _tpl()
    assert "All Scanners" not in t
    assert "Scanner P&amp;L Ranking" not in t and "Scanner P&L Ranking" not in t
    assert "<th>Scanner</th>" not in t


def test_scanner_attribution_screen_is_untouched(client):
    """⛔ Removing scanner from Screen 09 must NOT remove it from the Scanner
    Attribution screen, which is a different question with its own endpoint."""
    d = client.get("/api/scanner-attribution?period=week").get_json()
    assert d["rows"], "scanner attribution must still return its own rows"


# ── Override 2: TIME immediately after TRADING DATE ──────────────────────────
def test_time_column_is_second():
    t = _tpl()
    head = t.split("<thead>", 1)[1].split("</thead>", 1)[0]
    ths = re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)
    labels = [re.sub(r"\s+", " ", x).strip() for x in ths]
    assert labels[0] == "Trading Date"
    assert labels[1] == "Time"
    assert labels[2] == "Strategy"
    assert labels[:6] == ["Trading Date", "Time", "Strategy", "Symbol",
                          "Trade Type", "Direction"]
    assert "Scanner" not in labels


def test_time_is_a_real_measured_value_not_invented(client):
    """Time is the group's FIRST ENTRY time — a real column value. The fixture's
    closed trades all enter at 10:06:00."""
    d = client.get("/api/analytics/pnl?period=today").get_json()
    assert d["rows"], "fixture has closed trades today"
    for r in d["rows"]:
        assert r["time"] == "10:06:00"
    assert "first ENTRY time" in d["time_note"]


# ── Override 3: Screen-08 alignment BY COLUMN ROLE ───────────────────────────
def test_alignment_is_by_column_role_not_blanket():
    css, t = _css(), _tpl()
    # headings: centred, but LABEL headings explicitly left
    assert ".pnl-page .cap-table th { text-align: center; }" in css
    assert ".pnl-page .cap-table th.lbl { text-align: left; }" in css
    # ⛔ never a blanket rule over every cell
    assert ".pnl-page table td { text-align: center" not in css
    assert ".pnl-page .cap-table td { text-align: center" not in css
    # label headings carry the role in the markup, not a positional guess
    assert '<th class="lbl">Trading Date</th>' in t
    assert '<th class="lbl">Strategy</th>' in t
    # data cells use the codebase's own data marker
    assert 'class="ctr"' in t


def test_label_cells_stay_left():
    """Trading Date and Strategy body cells are plain <td> — the global centring
    rule only targets td.cap-num/.dt-num/.rt/.ctr, so labels are untouched."""
    t = _tpl()
    assert '<td x-text="r.date"></td>' in t
    assert '<td x-text="r.strategy"></td>' in t


# ── §11 ONE filtered population behind every panel ───────────────────────────
def test_totals_equal_the_sum_of_table_rows(client):
    """A PROPERTY, ⛔ not a magic number: the KPI row and the table must describe
    the same set."""
    d = client.get("/api/analytics/pnl?period=month").get_json()
    assert round(sum(r["net"] for r in d["rows"]), 2) == d["totals"]["net"]
    assert round(sum(r["gross"] for r in d["rows"]), 2) == d["totals"]["gross"]
    assert sum(r["trades"] for r in d["rows"]) == d["totals"]["trades"]
    assert sum(r["wins"] for r in d["rows"]) == d["totals"]["wins"]
    assert sum(r["losses"] for r in d["rows"]) == d["totals"]["losses"]


def test_every_panel_narrows_together(client):
    """Filtering by strategy must move KPI, table, rankings, curve, drawdown,
    heatmaps AND attribution — ⛔ no panel may keep the unfiltered population."""
    full = client.get("/api/analytics/pnl?period=week").get_json()
    one = client.get("/api/analytics/pnl?period=week&strategy=gap_fade_long").get_json()

    assert one["trade_count"] < full["trade_count"]
    assert one["trade_count_unfiltered"] == full["trade_count"]      # options keep full range
    assert one["totals"]["trades"] == one["trade_count"]
    assert sum(r["trades"] for r in one["rows"]) == one["trade_count"]
    assert [r["strategy"] for r in one["per_strategy"]] == ["gap_fade_long"]
    assert len(one["equity_curve"]["net"]) == one["trade_count"]
    assert len(one["equity_curve"]["gross"]) == one["trade_count"]
    assert sum(c["trades"] for c in one["heatmap_dow"]) == one["trade_count"]
    assert sum(c["trades"] for c in one["heatmap_tod"]) == one["trade_count"]
    assert round(sum(s["net"] for s in one["attribution"]["slices"]), 2) == one["totals"]["net"]
    # and the filter is echoed back so the UI can state it
    assert one["filters"]["active"] == {"strategy": "gap_fade_long"}


def test_filter_options_survive_filtering(client):
    """Choosing one value must not erase the other choices from the dropdown."""
    one = client.get("/api/analytics/pnl?period=week&strategy=gap_fade_long").get_json()
    assert len(one["filters"]["options"]["strategy"]) > 1


# ── §6 formulas (already verified against the code; pinned here) ─────────────
def test_formulas_are_the_documented_ones(client):
    d = client.get("/api/analytics/pnl?period=today").get_json()
    t = d["totals"]
    # fixture today: -100, -50, -75, +200 → net -25, 1 win / 3 losses, margin 4×5000
    assert t["net"] == -25.0 and t["trades"] == 4
    assert t["wins"] == 1 and t["losses"] == 3
    assert t["win_rate"] == round(100.0 * 1 / 4, 1) == 25.0
    assert t["profit_factor"] == round(200.0 / 225.0, 2) == 0.89
    # ⚠️ -0.125 rounds to -0.12, NOT -0.13: Python uses banker's rounding. The
    # property (100 × net / Σmargin, rounded to 2) is asserted FIRST and the
    # literal second, so a future edit cannot quietly "fix" the formula to match
    # a hand-computed constant.
    assert t["roi_pct"] == round(100.0 * -25.0 / 20000.0, 2) == -0.12


def test_win_loss_excludes_scratch_trades():
    """net == 0 is NEITHER a win nor a loss, so trades != wins + losses. The
    screen states this rather than letting the columns look like they misadd."""
    agg = ap._blank_agg("scope", "x")
    ap._add(agg, 0.0, 0.0, 0.0, 100.0)
    assert agg["trades"] == 1 and agg["wins"] == 0 and agg["losses"] == 0
    assert ap._metrics(agg)["win_rate"] is None


# ── §7 trade type via the authoritative ENTRY order ──────────────────────────
def test_trade_type_comes_from_the_entry_order(client):
    """The fixture gives trd_c1..c4 an ENTRY order with product MIS."""
    d = client.get("/api/analytics/pnl?period=today").get_json()
    assert {r["trade_type"] for r in d["rows"]} == {"MIS"}


def test_trade_type_missing_entry_is_UNKNOWN_not_invented(client):
    """Week/month trades have NO ENTRY order row. They must appear as UNKNOWN and
    remain COUNTED — ⛔ never defaulted to MIS and ⛔ never dropped."""
    d = client.get("/api/analytics/pnl?period=month").get_json()
    types = {r["trade_type"] for r in d["rows"]}
    assert "UNKNOWN" in types
    assert sum(r["trades"] for r in d["rows"]) == d["totals"]["trades"]


def test_unknown_trade_type_is_filterable(client):
    """A trade with no product must still be reachable — the whole point of a
    named bucket rather than a silent NULL."""
    d = client.get("/api/analytics/pnl?period=month&trade_type=UNKNOWN").get_json()
    assert d["trade_count"] > 0
    assert {r["trade_type"] for r in d["rows"]} == {"UNKNOWN"}


# ── §8 charges honesty ───────────────────────────────────────────────────────
def test_charges_report_whether_they_were_derived(client):
    d = client.get("/api/analytics/pnl?period=today").get_json()
    assert "charges_derived_rows" in d and "charges_note" in d
    # fixture persists charges = 5.0 on every closed trade
    assert d["charges_derived_rows"] == 0
    assert "persisted" in d["charges_note"]
    assert d["totals"]["charges"] == 20.0          # 4 × 5.0


# ── §8/§15 equity curve + drawdown ──────────────────────────────────────────
def test_curve_has_both_series_over_the_same_trades(client):
    d = client.get("/api/analytics/pnl?period=week").get_json()
    net, gross = d["equity_curve"]["net"], d["equity_curve"]["gross"]
    assert len(net) == len(gross) == d["trade_count"]
    assert [p["ts"] for p in net] == [p["ts"] for p in gross]      # same trades, same order
    assert net[-1]["cum"] == d["totals"]["net"]
    assert gross[-1]["cum"] == d["totals"]["gross"]


def test_curve_is_chronological_and_counts_each_trade_once(client):
    d = client.get("/api/analytics/pnl?period=month").get_json()
    ts = [p["ts"] for p in d["equity_curve"]["net"]]
    assert ts == sorted(ts)
    assert len(ts) == len(set(ts)) or len(ts) == d["trade_count"]


def test_curve_is_realized_only_no_fabricated_mtm(client):
    d = client.get("/api/analytics/pnl?period=week").get_json()
    assert "realized only" in d["curve_note"]
    assert "G4" in d["curve_note"]
    for key in ("mtm", "unrealized", "live_pnl"):
        assert key not in d


def test_drawdown_reports_unavailable_rather_than_zero():
    """⛔ A monotonic curve has NO drawdown. Reporting 0.00 would read as a
    measurement; `available: False` says there is nothing to measure."""
    dd = ap._drawdown([{"ts": "t1", "cum": 10.0}, {"ts": "t2", "cum": 20.0}])
    assert dd["available"] is False
    assert dd["max_drawdown"] is None and dd["recovery_pct"] is None
    assert ap._drawdown([])["available"] is False


def test_drawdown_maths():
    dd = ap._drawdown([
        {"ts": "2026-08-14T10:00:00", "cum": 100.0},
        {"ts": "2026-08-14T11:20:00", "cum": 300.0},
        {"ts": "2026-08-14T13:35:00", "cum": -50.0},
        {"ts": "2026-08-14T15:00:00", "cum": 120.0},
    ])
    assert dd["available"] is True
    assert dd["max_drawdown"] == -350.0            # 300 → -50
    assert dd["current_drawdown"] == -180.0        # 120 vs peak 300
    assert dd["recovery_pct"] == 48.57
    assert dd["duration"] == "2h 15m"
    assert dd["from"].endswith("11:20:00") and dd["to"].endswith("13:35:00")


def test_drawdown_follows_the_selected_series(client):
    d = client.get("/api/analytics/pnl?period=month").get_json()
    assert "drawdown" in d and "drawdown_gross" in d


# ── §9 heatmaps ──────────────────────────────────────────────────────────────
def test_dow_bucket_is_ist_weekday():
    assert ap._dow("2026-08-14T10:00:00") == "Fri"     # 14-Aug-2026 is a Friday
    assert ap._dow("2026-08-10T10:00:00") == "Mon"
    assert ap._dow("") is None


def test_tod_buckets_and_out_of_session():
    assert ap._tod_bucket("2026-08-14T09:40:00") == "09:15–10:00"
    assert ap._tod_bucket("2026-08-14T14:30:00") == "14:00–15:30"
    assert ap._tod_bucket("2026-08-14T16:00:00") is None      # outside the session
    assert ap._tod_bucket("2026-08-14T08:00:00") is None


def test_empty_buckets_are_marked_unobserved(client):
    """A bucket with no trades must be distinguishable from one that traded to
    exactly zero — ⛔ they must not look the same."""
    d = client.get("/api/analytics/pnl?period=today").get_json()
    assert any(c["observed"] is False for c in d["heatmap_dow"])
    for c in d["heatmap_dow"] + d["heatmap_tod"]:
        if not c["observed"]:
            assert c["trades"] == 0


def test_heatmap_totals_match_the_population(client):
    d = client.get("/api/analytics/pnl?period=today").get_json()
    assert sum(c["trades"] for c in d["heatmap_dow"]) == d["totals"]["trades"]


# ── §5 attribution cannot double-count ───────────────────────────────────────
def test_attribution_is_one_dimension_at_a_time(client):
    """Each dimension PARTITIONS the same net. ⛔ The PNG's five-slice legend
    would have summed strategy+symbol+direction over the same trades."""
    for dim in ("strategy", "symbol", "trade_type", "direction"):
        d = client.get(f"/api/analytics/pnl?period=month&attr={dim}").get_json()
        a = d["attribution"]
        assert a["dimension"] == dim
        assert round(sum(s["net"] for s in a["slices"]), 2) == d["totals"]["net"]


def test_attribution_shares_are_of_absolute_contribution(client):
    d = client.get("/api/analytics/pnl?period=month&attr=strategy").get_json()
    a = d["attribution"]
    pcts = [s["pct"] for s in a["slices"] if s["pct"] is not None]
    if pcts:
        assert abs(sum(pcts) - 100.0) < 0.05
    assert "absolute" in a["pct_basis"]


def test_attribution_defaults_safely_on_a_bad_dimension(client):
    d = client.get("/api/analytics/pnl?period=week&attr=nonsense").get_json()
    assert d["attribution"]["dimension"] == "strategy"


# ── §9 Compare With — real data or an explicit unavailability ───────────────
def test_compare_previous_uses_a_real_prior_window(client):
    d = client.get("/api/analytics/pnl?period=today&compare=previous").get_json()
    c = d["compare"]
    assert c["available"] is True and c["mode"] == "previous"
    assert c["from"] == c["to"] == YDAY          # a 1-day period compares to yesterday
    assert set(c["delta"]) >= {"gross", "charges", "net"}


def test_compare_window_is_equal_length_and_adjacent():
    assert ap._shift_range("2026-08-08", "2026-08-14") == ("2026-08-01", "2026-08-07")
    assert ap._shift_range("2026-08-14", "2026-08-14") == ("2026-08-13", "2026-08-13")


def test_unknown_compare_mode_is_declared_unavailable(client):
    """⛔ An unsupported comparison must say so, never render a fabricated one."""
    d = client.get("/api/analytics/pnl?period=today&compare=last_year").get_json()
    assert d["compare"]["available"] is False
    assert "no authoritative source" in d["compare"]["reason"]


def test_no_compare_requested_means_no_compare_block(client):
    d = client.get("/api/analytics/pnl?period=today").get_json()
    assert "compare" not in d


# ── §12 no silent row cap ────────────────────────────────────────────────────
def test_no_default_row_cap(client):
    d = client.get("/api/analytics/pnl?period=month").get_json()
    assert d["row_cap"] is None
    assert d["row_cap_applied"] is False


def test_reader_returns_everything_by_default(gui_config):
    """The reader itself must not cap. An explicit limit still works and is then
    REPORTED rather than hidden."""
    from backend.readers import db_reader
    allr = db_reader.closed_trades_range(gui_config, "2000-01-01", "2100-01-01")
    capped = db_reader.closed_trades_range(gui_config, "2000-01-01", "2100-01-01", limit=2)
    assert len(capped) == 2 and len(allr) > 2


# ── §10 export goes through the approved gate ────────────────────────────────
def test_export_uses_the_flag_gated_component(client):
    """⛔ No raw export button and no new endpoint. Screen 09 uses the shared
    export_button() macro, which renders DISABLED while table_export_enabled is
    off — the policy is preserved, not bypassed."""
    t = _tpl()
    assert "export_button" in t
    assert 'class="btn-export"' not in t          # ⛔ not hand-rolled
    assert "/api/export/pnl" not in t             # ⛔ no new raw endpoint
    html = client.get("/pnl-analytics").get_data(as_text=True)
    assert "disabled" in html                     # flag default OFF ⇒ disabled control


# ── Existing surfaces stay untouched ────────────────────────────────────────
def test_today_scoped_pnl_endpoint_unchanged(client):
    d = client.get("/api/pnl").get_json()
    assert "realized" in d or "equity_curve" in d or "splits" in d


def test_page_renders(client):
    html = client.get("/pnl-analytics").get_data(as_text=True)
    assert "P&amp;L Analytics" in html or "P&L Analytics" in html
    assert "pnl-page" in html


# ── Regression: the day-of-week panel must never drop trades ─────────────────
def test_dow_buckets_never_drop_a_trade():
    """⛔ Mon–Fri was rendered unconditionally, so a Saturday/Sunday close
    vanished from the panel while staying in the totals. A weekend bucket is now
    appended ONLY when observed, so a normal week still renders five columns.

    ⚠️ CALENDAR-GATED: on a weekday this passes either way. It is written against
    the FUNCTION so it fails on any day if the bug returns."""
    weekday = [{"exit_time": "2026-08-14T10:00:00"}]          # Friday
    assert ap._dow_buckets(weekday) == ("Mon", "Tue", "Wed", "Thu", "Fri")

    saturday = [{"exit_time": "2026-08-15T10:00:00"}]         # Saturday
    assert ap._dow_buckets(saturday) == ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

    both = weekday + saturday + [{"exit_time": "2026-08-16T10:00:00"}]   # + Sunday
    assert ap._dow_buckets(both) == ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    # and the population is fully represented whichever day it is
    heat = ap._heatmap(saturday + weekday, lambda r: ap._dow(r.get("exit_time")),
                       ap._dow_buckets(saturday + weekday))
    assert sum(c["trades"] for c in heat) == 2


# ══════════════════════════════════════════════════════════════════════════
# EQUITY-CURVE AXIS LABELS (19-Aug-2026, authorised by Rama)
#
# `09. PnL_Analytics.png` draws a ₹ scale down the left of the equity curve and
# a session timeline underneath. The build had NEITHER — measured: 0 <svg text>
# elements on the whole page at 1920x1080 AND 1440x900.
#
# MEASURED AFTER, in the browser, with populated data at BOTH viewports:
#   9 labels · every one 13.00px · no overlap (min x-gap 90.8px @1920,
#   15.3px @1440) · 0 escaping the panel · 0 page-level horizontal overflow.
#
# ⚠️ THE TRAP THAT NEARLY SHIPPED, AND WHY THE GUTTER IS PIXELS:
#   this panel renders 449.4px wide at 1920 but only 222.6px at 1440
#   (`.pnl-row3` is `1.25fr 1fr 1fr`). A gutter expressed as a share of the
#   viewBox is therefore oversized at one width and CLIPPING at the other — the
#   first attempt clipped `-₹25` by 6.5px at 1440 and let `15:10` escape the
#   right edge at both. Fixed px padding + calc() positioning makes the
#   clearances IDENTICAL at both widths (28.0 / 4.6 / 4.5 px).
#
# ⚠️ AND THE SECOND TRAP: a 58px gutter passed with the FIXTURE's ~₹200 ticks
#   and still clipped the ARTWORK's `-₹40,000` by 0.08px (50.08px vs 50px).
#   The worst case had to be rendered and measured, ⛔ not extrapolated.
# ══════════════════════════════════════════════════════════════════════════
class TestEquityCurveAxisLabels:

    def _fn(self, name):
        t = _tpl()
        t = t[t.index(name):]
        return t[:t.index("\n    },")]

    def test_the_labels_are_html_not_svg_text(self):
        """⛔ <text> inside `.pnl-curve` would be WORSE than the Screen 10/11
        defect: that SVG carries `preserveAspectRatio="none"`, so a glyph would
        be stretched by a DIFFERENT factor on each axis — distorted, not merely
        mis-sized."""
        tpl = _tpl()
        curve = tpl[tpl.index('class="pnl-cx"'):tpl.index('pnl-curve-foot')]
        assert "<text" not in curve, "an SVG <text> appeared inside the equity curve"
        assert 'preserveAspectRatio="none"' in curve, (
            "the curve stopped stretching — re-measure before trusting the overlay maths")
        assert 'class="cxl-layer"' in curve and "curveLabels()" in curve

    def test_the_overlay_type_holds_the_13px_floor(self):
        css = _css()
        m = re.search(r"\.pnl-page \.cxl \{([^}]*)\}", css)
        assert m, ".pnl-page .cxl rule missing"
        fm = re.search(r"font-size:\s*([0-9.]+)px", m.group(1))
        assert fm and float(fm.group(1)) >= 13, m.group(1)

    def test_the_gutter_is_pixels_and_holds_the_artworks_widest_tick(self):
        """MEASURED: `-₹40,000` renders 50.08px at 13px. A 58px gutter leaves
        50px and CLIPPED IT by 0.08px; 64px leaves 56px and fits with 5.92px
        spare. ⛔ The fixture's own ~₹200 ticks could never have caught this."""
        css = _css()
        m = re.search(r"\.pnl-page \.pnl-cx \{([^}]*)\}", css)
        assert m, ".pnl-page .pnl-cx rule missing"
        rule = m.group(1)
        assert "position: relative" in rule
        pm = re.search(r"padding:\s*0\s+(\d+)px\s+(\d+)px\s+(\d+)px", rule)
        assert pm, "the fixed-px gutters are gone: %s" % rule
        right, bottom, left = (int(x) for x in pm.groups())
        assert left - 8 >= 51, (
            "left gutter %dpx leaves %dpx, under the 50.08px the artwork's widest "
            "tick measures" % (left, left - 8))
        assert right >= 12 and bottom >= 20, (right, bottom)
        assert "GUT_L: %d," % left in _tpl(), (
            "the JS gutter and the CSS padding disagree — the labels would sit "
            "off the plot")

    def test_the_global_curve_wrap_is_still_not_a_positioning_context(self):
        """⛔ Giving the GLOBAL `.curve-wrap` a position would reach every screen
        with a chart. The context is `.pnl-cx`, used only here."""
        css = _css()
        assert not re.search(r"^\.curve-wrap \{[^}]*position:", css, re.M)

    def test_no_axis_value_is_invented(self):
        """⭐ y ticks come from bounds() — the range yFor() already plots — and
        every x label is a point's own `ts`. ⛔ Nothing is synthesised."""
        y = self._fn("yTicks() {")
        assert "this.bounds()" in y and "this.yFor(" in y
        x = self._fn("xTicks() {")
        assert "this.series()" in x and "s[i].ts" in x
        assert "Math.random" not in _tpl()

    def test_the_x_axis_carries_the_last_label_collision_rule(self):
        """⭐ Carried from Screens 11/12, ⛔ not re-invented: keep the session-end
        mark, drop the stepped one within half a step of it."""
        code = re.sub(r"/\*.*?\*/", "", self._fn("xTicks() {"), flags=re.S)
        assert "marks.pop()" in code and "Math.ceil(step / 2)" in code

    def test_the_shared_money_helper_was_not_repurposed(self):
        """The axis format `₹40,000` is the ARTWORK's, and is NOT money() (2dp,
        no grouping). ⛔ money() is shared with every screen and stays untouched."""
        a = self._fn("axisMoney(v) {")
        assert "toLocaleString" in a
        # ⛔ `_tpl()` IGNORES its argument and always reads pnl_analytics.html, so
        # `_tpl("base.html")` silently returns the WRONG FILE. base.html is read
        # directly here. (This test caught that on its own first run.)
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "frontend", "templates", "base.html"),
                  encoding="utf-8") as fh:
            base = fh.read()
        assert "Math.abs(n).toFixed(2)" in base and "money(v)" in base, \
            "the shared money() helper changed"
        assert "toLocaleString" not in base, \
            "the axis grouping leaked into the shared money() helper"

    def test_only_screen_09_scopes_were_added_to_the_css(self):
        """⛔ Strictly S09-scoped: every rule this change added names `.pnl-page`."""
        css = _css()
        block = css[css.index("SCREEN 09 — P&L ANALYTICS"):css.index("SCREEN 10 — SLIPPAGE")]
        for sel in re.findall(r"^([.#][^{\n]+)\{", block, re.M):
            sel = sel.strip()
            if sel.startswith("@"):
                continue
            assert sel.startswith(".pnl-page"), "unscoped rule in the S09 block: %s" % sel
