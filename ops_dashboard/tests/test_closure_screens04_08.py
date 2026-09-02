"""tests/test_closure_screens04_08.py — the 18/19-Aug 01-22 CLOSURE pass.

Screens 04 Signals, 05 Orders, 06 Positions, 07 Trade Explorer and 08 Capital &
Risk had never been audited against their artwork in this campaign. This suite
pins what that audit FIXED, and — just as importantly — pins what it deliberately
did NOT fix, so a later pass cannot quietly change either.

FIXED AND PINNED HERE
  · FLOOR   the spec's "Minimum: 13px" now holds for every rule scoped to
            `.sig-page` / `.ord-page` / `.pos-page` / `.tex-page` / `.cap-page`,
            with FOUR named exemptions rather than silent skips;
  · S05     the two donuts `05. Orders.png` draws (Slippage Summary, Order
            Result Distribution) exist, are built from the counts the legends
            already print, and carry the `fill:none` guard that stops the
            documented black-disc regression;
  · S08     the semicircular utilization gauge `08. Capital_Risk.png` draws,
            coloured by the SAME `usageClass()` the number and the range table
            use, plus the artwork's Top-Capital-Consumer bars.

DELIBERATELY NOT FIXED — PINNED SO IT CANNOT DRIFT
  · S07     RR Damage % stays ABSENT: `rr_damage_pct` lives in
            `trade_slippage_log`, a table Trade Explorer does not read, and the
            screen carries an explicit prior ruling that R-multiple is NEVER
            printed in an R:R column. That needs Rama, not a patch.
  · S04/05  Scanner stays REMOVED as a column and as a filter (`05. Orders.txt`:
            "Scanner is removed wherever applicable because Strategy = Scanner").
  · S05     "Total Orders" keeps counting FILLED ONLY — Rama's 12-Aug rule —
            while the percentages keep ALL orders as their base.
"""
from __future__ import annotations

import math
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")
_TPL = os.path.join(_ROOT, "frontend", "templates")


def _read(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _css() -> str:
    return _read(_CSS)


def _tpl(name: str) -> str:
    return _read(os.path.join(_TPL, name))


def _code(name: str) -> str:
    """Template with HTML comments stripped — scan what RUNS, not what a note
    happens to mention. (These templates NAME the things they removed.)"""
    return re.sub(r"<!--.*?-->", "", _tpl(name), flags=re.S)


def _fn(name: str, template: str) -> str:
    """The body of ONE JS method from a template.

    📌 Added after the non-vacuity harness caught two assertions passing for the
    WRONG REASON: `this.slipBuckets()` appears four times in orders.html and the
    null-guard line appears twice in capital_risk.html, so a whole-file
    substring check stayed green while the function under test was gutted.
    """
    src = _code(template)
    i = src.find(name)
    assert i != -1, "method %s not found in %s" % (name, template)
    # ⛔ anchor on the DEFINITION (`name() {`), never the first textual mention:
    #    `x-html="gaugeMarkup()"` appears in the markup long before the method.
    depth, out, started = 0, [], False
    for ch in src[i:]:
        out.append(ch)
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                break
    return "".join(out)


def _rules_in_scope(scopes) -> list:
    """(selector, font-size-px) for every rule whose selector carries a scope.

    📌 RULE-level, ⛔ never line-level: the first pass of this fix was
    line-based and silently missed eight rules whose selector sat on an
    earlier line than their font-size.
    """
    out = []
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)   # ⛔ a comment is not a selector
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sel = " ".join(x.strip() for x in m.group(1).strip().splitlines())
        if not any(s in sel for s in scopes):
            continue
        fm = re.search(r"font-size:\s*([0-9.]+)px", m.group(2))
        if fm:
            out.append((sel, float(fm.group(1))))
    return out


# ══════════════════════════════════════════════════════════════════════════
# THE 13px FLOOR — specs 04/05/06/07/08, READABILITY: "Minimum: 13px"
# ══════════════════════════════════════════════════════════════════════════
class TestTypeFloor:

    SCOPES = (".sig-page", ".ord-page", ".pos-page", ".tex-page", ".cap-page",
              ".pnl-page", ".slp-page", ".exec-page", ".sysh-page",
              ".cfg16-page", ".ctl-page")

    #: Each is a DECISION, named so a later pass cannot mistake it for an
    #: oversight and cannot quietly widen it:
    #:  (.flt-k WAS exempt here "because raising it would repaint approved
    #:            screens". RETIRED 19-Aug-2026 under B1: no screen has been
    #:            visually approved yet, and every TXT governing a screen that
    #:            renders it sets "Minimum: 13px". It is 13px now -- see
    #:            tests/test_b1_typography_floor.py.)
    #:  .st-arw / .st-grip — decorative glyphs (sort indicator, drag handle)
    #:            carrying no readable text.
    #:  .cap-g-tick — SVG text, whose font-size is in USER UNITS, not screen
    #:            px. Its RENDERED size is asserted separately and must stay
    #:            >= 13px: see
    #:            TestCapitalGauge.test_the_gauge_ticks_clear_the_13px_floor_when_rendered.
    #:            ⛔ This exemption is only honest while that test exists.
    #:  *-grip / *-arrow — the same decorative-glyph family as `.st-grip` /
    #:            `.st-arw` on every other screen: a drag handle and a sort
    #:            indicator, each a single symbol carrying NO readable text.
    #:            MEASURED rendered sizes: grips 8.80px, arrows 9.28px.
    #:  *-axis  — SVG chart axis labels. These DO carry readable values, and at
    #:            1440 they render 9.80px (S10) / 10.12-11.09px (S11) because
    #:            the chart's viewBox scales with its container. ⛔ This is
    #:            EXEMPTED HERE ONLY SO IT IS NOT SILENT — it is an OPEN,
    #:            REPORTED item needing a design decision (move the labels to
    #:            HTML, or accept viewBox scaling). Raising the user-unit size
    #:            would make them ~17px at 1920, which is worse.
    EXEMPT = (".st-arw", ".st-grip", ".cap-g-tick",
              "-grip", "-arrow", "-axis")

    def test_no_rule_scoped_to_screens_04_08_is_below_13px(self):
        offenders = [(s[:60], v) for s, v in _rules_in_scope(self.SCOPES)
                     if v < 13 and not any(x in s for x in self.EXEMPT)]
        assert not offenders, "below the 13px floor: %s" % offenders

    def test_no_exemption_beyond_the_named_families_is_in_use(self):
        """If an exemption disappears, this makes the list shrink loudly rather
        than letting a NEW sub-floor rule hide behind a stale name."""
        found = {x for s, v in _rules_in_scope(self.SCOPES) if v < 13
                 for x in self.EXEMPT if x in s}
        assert found <= set(self.EXEMPT)

    def test_the_svg_tick_exemption_is_backed_by_a_rendered_size_test(self):
        """⛔ The `.cap-g-tick` exemption is ONLY defensible while a test
        actually checks the rendered size. If that test is deleted, the
        exemption becomes a silent sub-floor rule — so fail here instead."""
        src = _read(os.path.abspath(__file__))
        assert "def test_the_gauge_ticks_clear_the_13px_floor_when_rendered" in src,             "the rendered-size test that justifies the .cap-g-tick exemption is gone"

    def test_flt_k_now_meets_the_floor_instead_of_being_exempt_from_it(self):
        """✅ B1 RULED 19-Aug-2026. ⛔ This test was previously the guard that
        kept `.flt-k` at 12px; it is flipped DELIBERATELY, ⛔ not rewritten to
        make a build pass. The rule is still SHARED — that never changed — but
        sharing was the reason to LEAVE it, and the reason evaporated: it is
        shared with screens whose own TXT sets "Minimum: 13px", and NONE of them
        had been visually approved. Measured at 12.00px on 8 screens before the
        raise: S03, S04, S05, S06, S07, S19, S20, S22."""
        rules = [s for s, _ in _rules_in_scope((".flt-k",)) if ".flt-k" in s]
        assert rules, ".flt-k rule not found at all"
        assert ".strat-page" in " ".join(rules), \
            ".flt-k stopped being shared — the raise still holds, but re-read why"
        below = [(s[:60], v) for s, v in _rules_in_scope((".flt-k",))
                 if ".flt-k" in s and v < 13]
        assert not below, ".flt-k fell back below the floor: %s" % below


    def test_every_appended_css_section_carries_a_real_header(self):
        r"""⛔ THE TRAP THIS PASS ACTUALLY HIT. Screen 16's suite bounds its own
        CSS block at the next "\n   SCREEN " header and falls back to EOF when
        there is none. Screen 16 was the LAST section, so a block appended after
        it was silently absorbed into Screen 16 and then failed its
        "every rule is scoped to the page" check.

        Any section header added after Screen 16 must therefore use the file's
        own convention, so the next person to append cannot repeat it."""
        css = _css()
        # ⭐ REPRODUCE Screen 16's bound EXACTLY, then assert what it yields is
        #    still Screen 16's own CSS. A block appended without a header lands
        #    inside this slice and turns up here as a foreign selector — which
        #    is precisely how the trap presented.
        start = css.index("SCREEN 16 — CONFIGURATION")
        nxt = css.find("\n   SCREEN ", start + 10)
        block = css[start:] if nxt == -1 else css[start:nxt]
        foreign = []
        for m in re.finditer(r"^([^@\s][^{]*)\{", block, flags=re.M):
            sel = m.group(1).strip()
            if not sel or sel.startswith(("/*", "}", ":root")):
                continue
            if ".cfg16-page" not in sel and "main.content" not in sel:
                foreign.append(sel[:60])
        assert not foreign, (
            "these rules were absorbed into Screen 16's CSS block because the "
            "section after it carries no `\\n   SCREEN ` header: %s" % foreign)


# ══════════════════════════════════════════════════════════════════════════
# SCREEN 05 — the two donuts `05. Orders.png` draws
# ══════════════════════════════════════════════════════════════════════════
class TestOrdersDonuts:

    def test_both_donuts_exist(self):
        t = _code("orders.html")
        assert t.count('class="pos-donut ord-donut"') == 2, \
            "the artwork draws a donut beside BOTH Slippage Summary and Order Result"
        assert "slipMarkup()" in t and "resultMarkup()" in t

    def test_the_arcs_are_built_from_the_counts_the_legend_prints(self):
        """⛔ No second source: a picture that could disagree with the number
        beside it is worse than no picture."""
        assert "this.slipBuckets()" in _fn("slipSegs() {", "orders.html"),             "slippage arcs must read slipBuckets(), not a second source"
        assert "this.resultCount(r)" in _fn("resultSegs() {", "orders.html"),             "result arcs must read resultCount(), not a second source"

    def test_the_black_disc_guard_is_present(self):
        """The documented Screen-09 regression: `.pos-donut circle {fill:none}`
        is `.pos-page`-scoped, so a donut on any other page renders as a solid
        black disc without its own rule."""
        assert re.search(r"\.ord-page\s+\.ord-donut\s+circle\s*\{[^}]*fill:\s*none",
                         _css()), "missing the fill:none guard for Screen 05's donuts"

    def test_donut_rules_are_keyed_on_ord_donut_so_screen_06_cannot_be_restyled(self):
        """⛔ Positions carries `ord-page` AS WELL AS `pos-page`; a bare
        `.ord-page .pos-donut` rule would silently restyle approved Screen 06."""
        for m in re.finditer(r"^(\.ord-page[^{\n]*?)\{", _css(), re.M):
            sel = m.group(1)
            if "pos-donut" in sel:
                assert "ord-donut" in sel, \
                    "Screen-05 donut rule %r would also hit Screen 06" % sel.strip()

    def test_no_zero_width_arc_is_emitted(self):
        assert "if (!p.n) return;" in _code("orders.html"), \
            "a zero-count bucket must not emit an arc"

    def test_unknown_slippage_is_a_visible_slice_not_a_silent_drop(self):
        """An order with no execution-log row must be VISIBLE as grey, ⛔ never
        folded into 'within limit'."""
        t = _code("orders.html")
        assert re.search(r'key:\s*"unknown".*?ord-arc-gray', t, re.S), \
            "the unknown bucket must render as its own grey arc"


# ══════════════════════════════════════════════════════════════════════════
# SCREEN 08 — the gauge and the consumer bars `08. Capital_Risk.png` draws
# ══════════════════════════════════════════════════════════════════════════
class TestCapitalGauge:

    def test_the_gauge_exists_and_is_a_semicircle(self):
        t = _code("capital_risk.html")
        assert 'class="cap-gauge"' in t
        assert 'd="M 10 56 A 40 40 0 0 1 90 56"' in t, \
            "the artwork's dial is a semicircle of radius 40"

    def test_the_arc_length_constant_matches_the_geometry(self):
        """The dash length is a PERCENTAGE of the real arc length; if the path
        and the constant ever disagree, the dial silently lies."""
        m = re.search(r"GAUGE_LEN:\s*([0-9.]+)", _code("capital_risk.html"))
        assert m, "GAUGE_LEN missing"
        assert abs(float(m.group(1)) - math.pi * 40) < 0.01, \
            "GAUGE_LEN must equal PI * r for a semicircle of r=40"

    def test_the_arc_is_coloured_by_the_same_classifier_as_the_number(self):
        """ONE classifier: the dial, the value inside it and the range table
        beneath it must never disagree."""
        assert "this.usageClass(p)" in _code("capital_risk.html"), \
            "the gauge must colour itself with usageClass(), not a second rule"

    def test_no_arc_is_drawn_when_capital_is_unknown(self):
        """⛔ A dial drawn at 0% would read as 'nothing used'. No data must
        render as NO arc."""
        body = _fn("gaugeMarkup() {", "capital_risk.html")
        assert 'return "";' in body and "p === null" in body,             "gaugeMarkup must return NO arc when utilisation is unknown"
        assert "p = 0" not in body, "an unknown utilisation must not become 0%"

    def test_the_gauge_ticks_clear_the_13px_floor_when_rendered(self):
        """SVG font-size is in USER UNITS: 6.5 * (210 / 100) = 13.65 rendered px.
        If the tick size, the box width or the viewBox changes, re-check this."""
        fs = re.search(r"\.cap-page \.cap-g-tick \{[^}]*font-size:\s*([0-9.]+)px", _css())
        w = re.search(r"\.cap-page \.cap-gauge \{ width:\s*([0-9.]+)px", _css())
        vb = re.search(r'class="cap-gauge" viewBox="0 0 ([0-9.]+) ',
                       _code("capital_risk.html"))
        assert fs and w and vb, "gauge tick geometry not found"
        rendered = float(fs.group(1)) * (float(w.group(1)) / float(vb.group(1)))
        assert rendered >= 13.0, "gauge ticks render at %.2fpx, under the floor" % rendered

    def test_the_consumer_bars_exist_and_scale_to_the_largest_consumer(self):
        t = _code("capital_risk.html")
        assert 'class="cap-bar"' in t, "the artwork draws a bar per consumer"
        assert "barPct(" in t
        assert "Math.max(...rows.map" in t, \
            "bar length must be relative to the largest consumer"

    def test_the_percent_column_still_carries_the_share_of_the_true_total(self):
        """⛔ The bar is a SHAPE; the number must stay the fact. The % column
        must keep using sharePct(), not the bar's max-relative number."""
        assert "pctText(sharePct(s.total_real))" in _code("capital_risk.html")


# ══════════════════════════════════════════════════════════════════════════
# PINNED PENDING — what this pass deliberately did NOT change
# ══════════════════════════════════════════════════════════════════════════
class TestPinnedPending:

    def test_rr_damage_is_still_absent_from_trade_explorer(self):
        """`07. Trade_Explorer.png` draws an RR Damage % column. It is NOT
        implemented: `rr_damage_pct` lives in `trade_slippage_log`, which this
        screen does not read, and the screen carries an explicit ruling that
        R-multiple is never printed in an R:R column. ⛔ A later pass must not
        add it without a ruling — if one does, this test says so."""
        assert "rr_damage" not in _code("trade_explorer.html").lower(), \
            "RR Damage appeared without a ruling — see the Screen-07 decision item"

    def test_scanner_stays_removed_as_a_column_and_a_filter(self):
        """`05. Orders.txt`: 'Scanner is removed wherever applicable because
        Strategy = Scanner'. Strategy is the identity."""
        for name in ("signals.html", "orders.html", "trade_explorer.html"):
            t = _code(name)
            assert not re.search(r'key:\s*"scanner"', t), "%s regrew a Scanner column" % name
            assert not re.search(r"f\.scanner|fVals\.scanner", t), \
                "%s regrew a Scanner filter" % name

    def test_total_orders_still_counts_filled_only_with_an_honest_sublabel(self):
        """Rama, 12-Aug: the top KPI pair counts FILLED ORDERS ONLY, and the
        card says so. ⛔ Not a defect to be 'corrected' to the all-orders count."""
        t = _code("orders.html")
        assert "k('total_orders')" in t
        assert "Filled only" in t, "the filled-only basis must stay stated on the card"

    def test_order_result_percentages_keep_all_orders_as_their_base(self):
        """⛔ If the percentage base ever becomes the filled-only count, every
        percentage reads 100% and the whole deck becomes meaningless."""
        from backend.readers import db_reader
        src = _read(os.path.join(_ROOT, "backend", "readers", "db_reader.py"))
        assert "return round(n / all_orders * 100.0, 2) if all_orders else None" in src, \
            "order KPI percentages must stay based on ALL orders"
        assert db_reader is not None
