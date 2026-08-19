"""tests/test_screen03_strategies.py — SCREEN 03 STRATEGIES ("Strategy Control Tower").

Design authority: `gui/03. Strategies.png` + `gui/03. Strategies.txt`.

This suite covers the AUTHORIZED 18-Aug visual-and-honesty pass (V1-V6, F2) and,
just as importantly, PINS THE ITEMS THAT WERE DELIBERATELY LEFT PENDING so a
later pass cannot quietly implement them without a ruling:

  · V1  both artwork sparklines (TOTAL P&L, WIN RATE) exist and come from REAL
        closed-trade rows — ⛔ never synthesised;
  · V2  numbered pagination, reusing approved Screen 22's `pageList()` shape;
  · V3  four family cards + "… and more" — a PRESENTATION cap that ⛔ does not
        truncate the data;
  · V4  no Screen-03-scoped declaration below the spec's 13px floor, with the
        shared `.flt-k` exemption named rather than silently skipped;
  · V5  the Date Range chip, with ONE calendar glyph;
  · V6  the dead Scanner-column CSS is gone;
  · F2  the orphan Scanner FILTER is gone, and no Scanner COLUMN returns;
  · ⏸️  D1/D2 capital, F1/Q3 export, F3 trading-type, F4 SL/TGT/ROI and the Q2
        taxonomy are asserted UNCHANGED — the export is asserted PENDING, ⛔ not
        converted into a passing export expectation.
"""
from __future__ import annotations

import inspect
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_TPL = os.path.join(_ROOT, "frontend", "templates", "strategies.html")
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")

from backend.readers import db_reader  # noqa: E402
from backend.services import strategy_tower  # noqa: E402


def _tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _tpl_code() -> str:
    """The template with HTML and `//` comments stripped.

    📌 Scan what RUNS: this template's comments deliberately NAME the Scanner
    filter and column they record the removal of, so a naive substring search
    finds them in the very note saying they are gone.
    """
    text = re.sub(r"<!--.*?-->", "", _tpl(), flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in text.splitlines())


def _screen03_css() -> str:
    """Only Screen 03's block, bounded at the next screen header.

    📌 ⛔ NEVER run to EOF — that is the defect this campaign has found four
    times: the block silently adopts whatever the next screen appends.
    """
    with open(_CSS, encoding="utf-8") as fh:
        css = fh.read()
    start = css.index("== Screen 03 — Strategies")
    stop = css.index("SCREEN-04", start)
    assert stop > start
    return css[start:stop]


# ══════════════════════════════════════════════════════════════════════════
# V1 — the artwork's two sparklines, from real rows
# ══════════════════════════════════════════════════════════════════════════
class TestSparklines:

    def test_both_artwork_sparklines_are_bound(self):
        tpl = _tpl()
        assert re.search(r'key: "pnl".*?spark: "pnl"', tpl, re.S), "TOTAL P&L spark missing"
        assert re.search(r'key: "winrate".*?spark: "winrate"', tpl, re.S), "WIN RATE spark missing"
        assert "sk-spark" in tpl, "no sparkline element in the KPI card"

    def test_the_series_come_from_real_closed_trades(self):
        """⛔ NOTHING SYNTHESISED: both series are today's CLOSED trades in the
        order they closed, read through the reader Screen 18 already uses."""
        src = inspect.getsource(strategy_tower.build_strategy_sparks)
        assert "activity_trade_exits" in src
        for banned in ("random", "linspace", "interpolate", "smooth"):
            assert banned not in src

    def test_pnl_series_is_cumulative_and_winrate_is_running(self, gui_config, today):
        sparks = strategy_tower.build_strategy_sparks(gui_config, today)
        assert set(sparks) == {"pnl", "winrate"}
        pnl, wr = sparks["pnl"]["points"], sparks["winrate"]["points"]
        assert len(pnl) == len(wr) == sparks["pnl"]["closed_trades"]
        for v in wr:
            assert 0.0 <= v <= 100.0, "a win rate outside 0-100: %r" % v

    def test_a_series_shorter_than_two_points_draws_no_line(self, gui_config, monkeypatch):
        """⛔ A single point is not a shape, and a flat line along the axis is
        still a DRAWN CHART — a reader takes a drawn chart as a trend."""
        monkeypatch.setattr(db_reader, "activity_trade_exits", lambda cfg, today: [])
        sparks = strategy_tower.build_strategy_sparks(gui_config)
        assert sparks["pnl"]["available"] is False
        assert sparks["winrate"]["available"] is False
        assert sparks["pnl"]["points"] == []

    def test_the_card_draws_nothing_when_unavailable(self):
        """The template must gate on `available`, not merely on the key existing."""
        tpl = _tpl()
        m = re.search(r"sparkSeries\(which\) \{(.*?)\n    \},", tpl, re.S)
        assert m, "sparkSeries not found"
        assert "available" in m.group(1)

    def test_the_two_lines_use_existing_tokens_only(self):
        """The artwork draws P&L green and Win Rate violet. ⛔ No new colour."""
        block = _screen03_css()
        assert ".strat-page .sk-spark .spark-line { stroke: var(--pos)" in block
        assert ".strat-page .sk-spark.spark-winrate .spark-line { stroke: var(--purple)" in block

    def test_screen_02s_spark_class_is_not_reused(self):
        """⛔ A DISTINCT class, so neither screen can move the other."""
        assert "kc-spark" not in _tpl_code(), "Screen 02's spark class leaked into Screen 03"


# ══════════════════════════════════════════════════════════════════════════
# V2 — numbered pagination, reusing approved Screen 22
# ══════════════════════════════════════════════════════════════════════════
class TestPagination:

    def test_the_pager_is_numbered(self):
        code = _tpl_code()
        assert "pageList()" in code, "no numbered page list"
        assert "st-pg-n" in code and "is-on" in code
        assert "tPage + ' / ' + tPageCount" not in code, "the old N / M pager is back"

    def test_pagelist_matches_screen_22s_approved_shape(self):
        """⭐ Reused, ⛔ not reinvented: same window and same ellipsis rule as
        approved Screen 22, so the two pagers cannot drift."""
        holdings = os.path.join(_ROOT, "frontend", "templates", "holdings.html")
        with open(holdings, encoding="utf-8") as fh:
            h = fh.read()
        for token in ("PAGE_WINDOW", "pageList()"):
            assert token in h, "Screen 22 no longer defines %s" % token
            assert token in _tpl(), "Screen 03 does not reuse %s" % token

    def test_the_spec_page_sizes_are_offered(self):
        """Spec: "Rows per page: 50 / 60 selectable"."""
        code = _tpl_code()
        assert code.count(':value="50"') >= 1 and code.count(':value="60"') >= 1


# ══════════════════════════════════════════════════════════════════════════
# V3 — hierarchy: a PRESENTATION cap, not a data cap
# ══════════════════════════════════════════════════════════════════════════
class TestHierarchy:

    def test_four_cards_then_and_more(self):
        code = _tpl_code()
        assert "HIER_SHOWN: 4" in code, "the artwork's four-card cap is missing"
        assert "hierarchyShown()" in code and "hierarchyOverflow()" in code
        assert "and ' + hierarchyOverflow() + ' more'" in code

    def test_the_cap_does_not_truncate_the_underlying_data(self):
        """⛔ `hierarchy()` must still hold EVERY family, and the table below must
        still page over the full filtered set — only the strip is capped."""
        code = _tpl_code()
        assert "hierarchyShown() { return this.hierarchy().slice(0, this.HIER_SHOWN); }" in code
        assert "tPaged(filtered())" in code, "the table no longer pages the full set"


# ══════════════════════════════════════════════════════════════════════════
# V4 — the spec's 13px floor
# ══════════════════════════════════════════════════════════════════════════
class TestTypeFloor:

    #: ⛔ `.flt-k` (12px) is the APPROVED shared filter-label language reaching
    #: Screens 19/20/22. It is named here so the exemption is a DECISION and not
    #: an oversight, and so a future pass cannot quietly "fix" it.
    SHARED_EXEMPT = (".flt-k",)

    def test_no_screen03_scoped_rule_is_below_13px(self):
        """`gui/03. Strategies.txt`, READABILITY: "Minimum 13px"."""
        block = _screen03_css()
        offenders = []
        for m in re.finditer(r"([^\n{}]*)\{([^}]*?)font-size:\s*([0-9.]+)px", block, re.S):
            sel = (m.group(1).strip().splitlines() or [""])[-1].strip()
            if float(m.group(3)) < 13 and not any(x in sel for x in self.SHARED_EXEMPT):
                offenders.append((sel[:52], m.group(3)))
        assert not offenders, "below the 13px floor: %s" % offenders

    def test_the_shared_exemption_still_exists_and_is_still_shared(self):
        """If `.flt-k` ever stops being shared, the exemption must be revisited."""
        block = _screen03_css()
        assert ".flt-k" in block
        assert ".sr-page .flt-k" in block and ".hld-page .flt-k" in block


# ══════════════════════════════════════════════════════════════════════════
# V5 — the Date Range chip
# ══════════════════════════════════════════════════════════════════════════
class TestDateRange:

    def test_the_chip_has_a_calendar_and_a_reload_glyph(self):
        code = _tpl_code()
        assert "flt-date" in code and "flt-date-rf" in code
        assert 'type="date"' in code, "the native date control was replaced"

    def test_exactly_one_calendar_glyph_is_shown(self):
        """⛔ FOUND BY RENDERING: the native date input paints its OWN picker
        indicator, which put a SECOND calendar inside the chip."""
        block = _screen03_css()
        assert "::-webkit-calendar-picker-indicator { display: none; }" in block

    def test_the_glyph_still_opens_the_same_picker(self):
        """V5 is presentation only — the control's behaviour must not change."""
        code = _tpl_code()
        assert "showPicker" in code, "the calendar glyph no longer opens the picker"


# ══════════════════════════════════════════════════════════════════════════
# V6 + F2 — Scanner is not an identity here
# ══════════════════════════════════════════════════════════════════════════
class TestScannerIsNotAnIdentity:

    def test_no_scanner_filter(self):
        code = _tpl_code()
        assert "All Scanners" not in code, "the Scanner filter is back"
        assert "scannerOptions" not in code
        assert "f.scanner" not in code and "a.scanner" not in code

    def test_no_scanner_column(self):
        """The table's identity IS the strategy; a Scanner column would print the
        row's own identity twice (scanner and strategy are 1:1)."""
        tpl = _tpl()
        m = re.search(r"cols: \[(.*?)\],\n", tpl, re.S)
        assert m, "column list not found"
        assert "scanner" not in m.group(1).lower(), "a Scanner column returned"

    def test_the_dead_scanner_css_is_gone(self):
        assert "st-scan-n" not in _screen03_css()
        assert "st-scan-n" not in _tpl()

    def test_strategy_remains_the_identity(self):
        tpl = _tpl()
        m = re.search(r"cols: \[(.*?)\],\n", tpl, re.S)
        assert 'key: "strategy"' in m.group(1)
        assert "strategyOptions()" in tpl, "the Strategy filter was lost"


# ══════════════════════════════════════════════════════════════════════════
# NO REGRESSION of the existing Strategy data contract
# ══════════════════════════════════════════════════════════════════════════
class TestNoDataRegression:

    # ⭐ UPDATED 19-Aug-2026 for Rama's Q3 ruling (F3 + F4: the TXT wins over the
    # artwork). The four ADDED keys are marked; ⛔ every pre-existing key keeps its
    # relative order, so this still catches a reordering or a silent removal.
    ARTWORK_COLUMNS = ["strategy",
                       "trade_type",                       # F3 — spec, main table, 2nd
                       "signals", "orders", "trades", "success", "win_rate",
                       "sl_hits", "tgt_hits",              # F4
                       "pnl",
                       "roi",                              # F4
                       "allocated", "used", "remaining", "usage",
                       "last_signal", "last_trade", "status"]
    PRE_Q3_COLUMNS = ["strategy", "signals", "orders", "trades", "success", "win_rate",
                      "pnl", "allocated", "used", "remaining", "usage",
                      "last_signal", "last_trade", "status"]

    def test_the_column_set_is_unchanged(self):
        tpl = _tpl()
        m = re.search(r"cols: \[(.*?)\],\n", tpl, re.S)
        keys = re.findall(r'key: "(\w+)"', m.group(1))
        assert keys == self.ARTWORK_COLUMNS, "the column set moved: %s" % keys

    def test_the_q3_additions_are_purely_additive(self):
        """⛔ Q3 authorised ADDING four columns — ⛔ not reordering or dropping the
        approved ones. Every pre-Q3 key must still be present in its original
        relative order."""
        keys = re.findall(r'key: "(\w+)"',
                          re.search(r"cols: \[(.*?)\],\n", _tpl(), re.S).group(1))
        kept = [k for k in keys if k in self.PRE_Q3_COLUMNS]
        assert kept == self.PRE_Q3_COLUMNS, (
            "an approved column was dropped or reordered: %s" % kept)
        assert set(keys) - set(self.PRE_Q3_COLUMNS) == {
            "trade_type", "sl_hits", "tgt_hits", "roi"}, "unexpected extra column"

    def test_the_tower_payload_still_carries_every_key_the_screen_reads(self, gui_config, today):
        tower = strategy_tower.build_strategy_tower(gui_config, today)
        for key in ("today", "rows", "rankings", "count", "sparks"):
            assert key in tower, "tower payload lost %r" % key
        if tower["rows"]:
            r = tower["rows"][0]
            for key in ("basic", "signals", "processing", "trading", "capital_view"):
                assert key in r

    def test_the_status_vocabulary_is_the_specs(self):
        """Spec STATUS LOGIC: ACTIVE / QUIET / SILENT."""
        code = _tpl_code()
        for s in ("ACTIVE", "QUIET", "SILENT"):
            assert s in code

    def test_the_kpi_labels_are_uppercase_as_the_artwork_draws_them(self):
        """The artwork draws "TOTAL STRATEGIES" / "ACTIVE" / "QUIET" / "SILENT" —
        verified off the PNG at 3x. The deck inherits `.dash-page .kc-label`,
        which carries NO transform, so Screen 03 rendered Title Case.

        ⭐ SCOPED: the rule must be `.strat-page`, ⛔ never a `.dash-page` edit —
        that would recase Screen 02's approved deck and every other consumer.
        ⛔ AND THE 13px FLOOR IS NOT TRADED FOR IT: Screens 04/05 pair uppercase
        with 11.5px; this rule must introduce NO font-size at all.
        """
        block = _screen03_css()
        m = re.search(r"\.strat-page \.kc-label \{([^}]*)\}", block)
        assert m, "no .strat-page .kc-label rule — labels would render Title Case"
        body = m.group(1)
        assert "text-transform: uppercase" in body
        assert "font-size" not in body, (
            "the uppercase rule must not restate a font-size; 13px stays inherited")

    def test_the_uppercase_rule_does_not_touch_other_screens(self):
        """⛔ Screen 02's deck and the shared `.dash-page` rule keep their casing."""
        with open(_CSS, encoding="utf-8") as fh:
            css = fh.read()
        m = re.search(r"\.dash-page \.kc-label \{([^}]*)\}", css)
        assert m, ".dash-page .kc-label missing"
        assert "text-transform" not in m.group(1), (
            "the shared label rule was recased — this would alter approved screens")

    def test_the_eight_artwork_kpi_cards_survive(self):
        tpl = _tpl()
        m = re.search(r"kpiCards\(\) \{(.*?)\n    \},", tpl, re.S)
        keys = re.findall(r'key: "(\w+)"', m.group(1))
        assert keys == ["total", "active", "quiet", "silent",
                        "signals", "trades", "pnl", "winrate"], keys


# ══════════════════════════════════════════════════════════════════════════
# ⏸️ PENDING — asserted UNCHANGED, so no later pass implements them silently
# ══════════════════════════════════════════════════════════════════════════
class TestPendingItemsRemainPending:

    def test_export_is_implemented_and_is_no_longer_the_disabled_stub(self):
        """✅ B6 RULED 19-Aug-2026. ⛔ This test was the PENDING guard asserting the
        export's ABSENCE; it is flipped DELIBERATELY, ⛔ not rewritten to make a
        build pass. `03. Strategies.txt` states EXPORT: "Download XLSX", the
        `_xlsx` writer and the `/api/export/*` pattern already ship on seventeen
        screens, and the Q3 copy-protection question it was waiting on is a
        data-egress question that those seventeen already answer in the
        affirmative.

        ⭐ Screen 03 uses the PER-SCREEN anchor, ⛔ not the shared macro: the macro
        is gated by ONE global flag, so enabling it would also un-disable
        Screens 04/08/09, which have no export route behind them.
        """
        code = _tpl_code()
        assert 'exportUrl()' in code, "Screen 03 lost its export URL builder"
        assert "/api/export/strategies" in code
        # ⛔ Assert the IMPORT and the CALL, ⛔ not the bare word: the template
        #    now EXPLAINS in a comment why it left the macro, and a substring
        #    check would match that explanation and fail on its own prose.
        assert "import export_button" not in _tpl(), (
            "Screen 03 re-imported the flag-gated macro")
        assert "{{ export_button(" not in _tpl(), (
            "Screen 03 is back on the flag-gated macro, which is disabled")
        api_dir = os.path.join(_ROOT, "backend", "api")
        found = False
        for fn in sorted(os.listdir(api_dir)):
            if fn.endswith(".py"):
                with open(os.path.join(api_dir, fn), encoding="utf-8") as fh:
                    if 'route("/api/export/strategies"' in fh.read():
                        found = True
        assert found, "no /api/export/strategies route exists"

    def test_the_shared_macro_is_still_gated_off_for_the_screens_that_use_it(self):
        """⛔ B6 must not have become a global flag flip. Screens 04/08/09 still
        render the DISABLED macro, because they have no export route."""
        comp = os.path.join(_ROOT, "frontend", "templates", "components.html")
        with open(comp, encoding="utf-8") as fh:
            assert "table_export_enabled" in fh.read(), (
                "the export flag gate disappeared from the shared macro")
        tdir = os.path.join(_ROOT, "frontend", "templates")
        still = []
        for fn in ("signals.html", "capital_risk.html", "pnl_analytics.html"):
            with open(os.path.join(tdir, fn), encoding="utf-8") as fh:
                if "export_button" in fh.read():
                    still.append(fn)
        assert len(still) == 3, "a screen left the gated macro silently: %s" % still

    def test_the_workbook_header_is_the_table_header_in_the_table_order(self):
        """⛔ An export that drifts from its screen is worse than no export. The
        column ORDER is asserted, not just membership."""
        code = _tpl_code()
        labels = re.findall(r'\{\s*key:\s*"[a-z_]+",\s*label:\s*"([^"]+)"', code)
        assert labels, "Screen 03's cols array could not be read"
        header = [h.replace("(Rs)", "(₹)") for h in strategy_tower.EXPORT_HEADER]
        assert header == labels, (
            "export header != table header: %s vs %s" % (header, labels))

    def test_the_export_applies_the_screens_own_four_filters(self):
        src = inspect.getsource(strategy_tower.export_rows)
        for f in ("strategy", "status", "trade_type", "direction"):
            assert f in src, "the export ignores the %s filter" % f

    def test_the_export_never_turns_an_unset_bucket_into_a_zero(self):
        """⭐ B5's capital truth, carried into the workbook: "no bucket configured"
        and "zero allocated" are DIFFERENT facts, and the export must not merge
        them. Fixture-independent — the payload is built by hand."""
        payload = {"rows": [{"basic": {"display_name": "s1", "trade_type": "INTRADAY",
                                       "direction": "LONG"},
                             "capital_view": {"capital_used": 10.0,
                                              "capital_remaining": None},
                             "signals": {}, "processing": {}, "trading": {},
                             "performance": {}, "health": {}, "silence": {}}]}
        rows = strategy_tower.export_rows(payload)
        alloc = rows[1][strategy_tower.EXPORT_HEADER.index("Allocated (Rs)")]
        usage = rows[1][strategy_tower.EXPORT_HEADER.index("Usage %")]
        assert alloc is None and usage is None, (alloc, usage)

    def test_the_export_endpoint_returns_a_real_workbook(self, client):
        r = client.get("/api/export/strategies")
        assert r.status_code == 200, r.status_code
        assert "spreadsheetml" in r.headers.get("Content-Type", "")
        assert r.data[:2] == b"PK", "not a zip container, so not an xlsx"
        assert "strategies_" in r.headers.get("Content-Disposition", "")


    def test_capital_arithmetic_is_untouched(self):
        """✅ B5 RULED 19-Aug-2026 — VALIDATED, ⛔ NO CODE CHANGE. (Formerly the
        "D1/D2 PENDING" guard. ⛔ This is the CAPITAL item, ⛔ not the already-closed
        PLACEMENT D1.)

        The derivation was checked end to end and is internally consistent:
            capital_used      = the strategy's own open margin
            capital_remaining = bucket_limit − that same margin
            bucket_limit      = intraday_bucket_pct x opening   (GLOBAL)
        ⇒ the screen's `used + remaining` resolves to the global intraday bucket,
        which is exactly what `allocation_basis: "global bucket"` declares. No
        per-strategy cap is configured, and the spec does not require one, so
        nothing is implemented.

        ⚠️ RECORDED, ⛔ not fixed: each row subtracts only ITS OWN margin from the
        SHARED bucket, so Remaining is honest PER ROW but is ⛔ NOT ADDITIVE across
        rows — two strategies can each show headroom that is the same rupees. That
        is a property of the declared basis, ⛔ not an inconsistency in it, and it
        is why the basis is declared rather than assumed.

        The guard STAYS, and is now deliberate rather than provisional: it fails
        if the derivation moves or if a per-strategy cap appears without a ruling.
        """
        code = _tpl_code()
        assert "const alloc = (rem === null) ? null : (used + rem);" in code, (
            "the capital derivation changed while D1/D2 are pending")
        src = inspect.getsource(strategy_tower.build_strategy_tower)
        assert '"allocation_configured": None' in src
        assert '"allocation_basis": "global bucket"' in src
        # ⛔ The bucket must stay GLOBAL. If a per-strategy key ever feeds
        #    `bucket_limit`, the declared basis becomes a false statement --
        #    so fail here rather than let the label and the arithmetic diverge.
        assert 'cap_cfg.get("intraday_bucket_pct")' in src, (
            "bucket_limit no longer comes from the global intraday bucket")
        assert "per_strategy" not in src, (
            "a per-strategy allocation key appeared without a ruling")

    def test_the_trading_type_column_exists_and_sits_where_the_spec_puts_it(self):
        """✅ F3 RULED 19-Aug-2026 (Rama Q3 = the TXT wins over the artwork).
        ⛔ This test was previously the PENDING guard asserting its ABSENCE; it is
        flipped deliberately, ⛔ not rewritten to make a build pass.
        `03. Strategies.txt` puts `Trading type: (INTRADAY/DELIVERY)` SECOND, right
        after Strategy. ⛔ Scanner stays absent (approved override: scanner=strategy)."""
        tpl = _tpl()
        m = re.search(r"cols: \[(.*?)\],\n", tpl, re.S)
        keys = re.findall(r'key: "([a-z_]+)"', m.group(1))
        assert "trade_type" in keys, "the Trading Type column is gone"
        assert keys.index("trade_type") == 1, (
            "Trading Type must sit immediately after Strategy, per the spec: %s" % keys[:4])
        assert "scanner" not in keys, "Scanner came back as a column"

    def test_the_sl_tgt_and_roi_fields_exist_and_invent_nothing(self):
        """✅ F4 RULED 19-Aug-2026. ⭐ All three were ALREADY in the payload and
        were simply never rendered — `sl_tgt_hits.{sl_hits,tgt_hits}` and
        `performance.roi_pct`. ⛔ No backend read was added and no value derived
        in the UI; the ROI base stays attribution R4 (net / Σ margin_reserved)."""
        code = _tpl_code()
        assert "r.sl_tgt_hits" in code and "r.performance.roi_pct" in code
        keys = re.findall(r'key: "([a-z_]+)"', re.search(r"cols: \[(.*?)\],\n", _tpl(), re.S).group(1))
        for k in ("sl_hits", "tgt_hits", "roi"):
            assert k in keys, "%s column missing" % k
        # ⛔ null must survive: "no margin reserved today" is NOT "0% return".
        assert "roi_pct === null" in code and "? null :" in code, (
            "roi was coerced to a number — a missing base would read as 0%")
        src = inspect.getsource(strategy_tower.build_strategy_tower)
        assert "roi_pct" in src and "sl_tgt_hits" in src, (
            "the backend stopped supplying what the UI now renders")

    def test_the_table_body_still_has_one_cell_per_declared_column(self):
        """⛔ The body is hand-written <td>s matched POSITIONALLY to `cols`, so a
        column added without its cell silently shifts every value one column
        left. This counts both sides."""
        tpl = _tpl()
        cols = re.findall(r'key: "([a-z_]+)"',
                          re.search(r"cols: \[(.*?)\],\n", tpl, re.S).group(1))
        body = tpl[tpl.index('<template x-for="(r, i) in tPaged(filtered())"'):]
        body = body[:body.index("</template>")]
        assert body.count("<td") == len(cols), (
            "%d <td> for %d columns — the row is misaligned" % (body.count("<td"), len(cols)))

    def test_the_q2_rejection_taxonomy_is_untouched(self):
        """⏸️ Q2 PENDING. Screen 03 does not render the reject split, so this pass
        required no taxonomy change — and the tuples must stay as they are."""
        assert len(db_reader._RISK_REJECT_STATUSES) == 10
        assert len(db_reader._CAPITAL_REJECT_STATUSES) == 3
        code = _tpl_code()
        for banned in ("risk_rej", "capital_rej"):
            assert banned not in code, "Screen 03 started rendering %s" % banned
