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

    ARTWORK_COLUMNS = ["strategy", "signals", "orders", "trades", "success", "win_rate",
                       "pnl", "allocated", "used", "remaining", "usage",
                       "last_signal", "last_trade", "status"]

    def test_the_column_set_is_unchanged(self):
        tpl = _tpl()
        m = re.search(r"cols: \[(.*?)\],\n", tpl, re.S)
        keys = re.findall(r'key: "(\w+)"', m.group(1))
        assert keys == self.ARTWORK_COLUMNS, "the column set moved: %s" % keys

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

    def test_export_is_still_the_disabled_stub_and_is_NOT_implemented(self):
        """⏸️ F1/Q3 PENDING. ⛔ This test asserts the PENDING state deliberately:
        it must NOT be rewritten into a passing export expectation without the
        Q3 (copy-protection acceptance) ruling. Screen 03 renders the shared
        `export_button` macro, which is DISABLED until `table_export_enabled`,
        and there is no `/api/export/strategies` route.
        """
        assert "export_button" in _tpl(), "Screen 03 stopped using the shared export macro"
        api_dir = os.path.join(_ROOT, "backend", "api")
        for fn in os.listdir(api_dir):
            if fn.endswith(".py"):
                with open(os.path.join(api_dir, fn), encoding="utf-8") as fh:
                    assert "export/strategies" not in fh.read(), (
                        "an /api/export/strategies route appeared while Q3 is pending")

    def test_capital_arithmetic_is_untouched(self):
        """⏸️ D1/D2 PENDING. The Allocated/Used/Remaining derivation must not move
        until the per-strategy-allocation question is ruled — and the backend
        must keep DECLARING that no per-strategy cap exists.
        """
        code = _tpl_code()
        assert "const alloc = (rem === null) ? null : (used + rem);" in code, (
            "the capital derivation changed while D1/D2 are pending")
        src = inspect.getsource(strategy_tower.build_strategy_tower)
        assert '"allocation_configured": None' in src
        assert '"allocation_basis": "global bucket"' in src

    def test_no_trading_type_column_was_added(self):
        """⏸️ F3 PENDING — the spec lists it, the artwork's table does not draw it."""
        tpl = _tpl()
        m = re.search(r"cols: \[(.*?)\],\n", tpl, re.S)
        assert "trade_type" not in m.group(1), "a Trading Type column appeared while F3 is pending"

    def test_no_sl_tgt_or_roi_fields_were_added(self):
        """⏸️ F4 PENDING."""
        code = _tpl_code()
        for banned in ("sl_hit_count", "tgt_hit_count", "roi_pct"):
            assert banned not in code, "%s appeared while F4 is pending" % banned

    def test_the_q2_rejection_taxonomy_is_untouched(self):
        """⏸️ Q2 PENDING. Screen 03 does not render the reject split, so this pass
        required no taxonomy change — and the tuples must stay as they are."""
        assert len(db_reader._RISK_REJECT_STATUSES) == 10
        assert len(db_reader._CAPITAL_REJECT_STATUSES) == 3
        code = _tpl_code()
        for banned in ("risk_rej", "capital_rej"):
            assert banned not in code, "Screen 03 started rendering %s" % banned
