"""tests/test_s1011_chart_labels.py — SCREENS 10 & 11 CHART LABELS.

Design authority: `gui/10. Slippage_Analytics.png` + `.txt`,
                  `gui/11. Execution_Analytics.png` + `.txt`  (READABILITY: 13px).

THE DEFECT THESE PIN
  SVG text declares font-size in USER UNITS, so its RENDERED size is
  declared_px x (container_width / viewBox_width). With a FIXED viewBox that is
  1:1 at exactly ONE viewport. Measured before the fix:

      S10 trend chart      13.00px @1920   9.80px @1440
      S11 throughput       13.00px @1920  11.09px @1440
      S11 delay trend      12.41px @1920  10.12px @1440

  ⛔ Raising the user-unit size is NOT a fix: it would make the same labels
  ~17px at 1920. The labels were therefore moved OUT of the SVG into an HTML
  overlay positioned as a percentage of the same viewBox, so geometry still
  scales while type is real CSS px. Measured after: 13.00px at BOTH viewports
  on both screens, 0 overlaps, 0 labels escaping their panel.

WHAT IS ASSERTED
  · the labels still EXIST, and still come from the same tick sources;
  · nothing re-introduces <text> into these chart bodies;
  · the overlay's viewBox constants MATCH the <svg viewBox> they divide by —
    a mismatch would silently put every label in the wrong place;
  · markup and labels share ONE geometry function, so they cannot drift;
  · the throughput x-axis never prints two labels closer than half a step
    (the collision that measured 4px at 1920 and 12px at 1440);
  · Screen 10's rail tables scroll inside their panel instead of pushing the
    PAGE sideways.
"""
from __future__ import annotations

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
    mentions. These templates deliberately NAME the SVG text they removed."""
    return re.sub(r"<!--.*?-->", "", _tpl(name), flags=re.S)


def _fn(name: str, template: str) -> str:
    """The body of one JS method, brace-matched."""
    src = _code(template)
    i = src.find(name)
    assert i != -1, "method %s not found in %s" % (name, template)
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


# ══════════════════════════════════════════════════════════════════════════
# THE LABELS LEFT THE SVG — and nothing may put them back
# ══════════════════════════════════════════════════════════════════════════
class TestLabelsAreHtml:

    def test_screen10_chart_body_emits_no_svg_text(self):
        body = _fn("chartMarkup() {", "slippage.html")
        assert "<text" not in body, \
            "SVG text in the chart body cannot hold 13px at every viewport"

    def test_screen11_chart_bodies_emit_no_svg_text(self):
        for fn in ("tpMarkup() {", "trendMarkup() {"):
            assert "<text" not in _fn(fn, "execution.html"), \
                "%s re-introduced SVG text" % fn

    def test_the_dead_axis_helper_is_gone(self):
        """`_axis()` only existed to emit SVG <text>; leaving it invites a
        later pass to call it again."""
        assert "_axis(" not in _code("execution.html")

    def test_both_screens_render_an_html_overlay(self):
        assert 'x-for="(l, i) in chartLabels()"' in _code("slippage.html")
        ex = _code("execution.html")
        assert 'x-for="(l, i) in tpLabels()"' in ex
        assert 'x-for="(l, i) in trendLabels()"' in ex

    def test_overlay_labels_are_declared_at_the_13px_floor(self):
        css = _css()
        for scope in (".slp-page", ".exec-page"):
            m = re.search(re.escape(scope) + r" \.cxl \{[^}]*font-size:\s*([0-9.]+)px", css)
            assert m, "%s .cxl rule missing" % scope
            assert float(m.group(1)) >= 13, "%s .cxl is %spx" % (scope, m.group(1))

    def test_the_positioning_context_is_page_scoped_not_the_global_wrapper(self):
        """⛔ `.curve-wrap` is GLOBAL. Giving it `position: relative` here would
        change every screen that uses a chart."""
        css = _css()
        assert not re.search(r"^\.curve-wrap \{[^}]*position:", css, re.M), \
            "the global .curve-wrap must not be given a position"
        assert ".slp-page .slp-cx { position: relative; }" in css
        assert ".exec-page .exec-cx { position: relative; }" in css


# ══════════════════════════════════════════════════════════════════════════
# THE LABELS STILL SAY THE RIGHT THING, IN THE RIGHT PLACE
# ══════════════════════════════════════════════════════════════════════════
class TestLabelsAreCorrect:

    def test_screen10_labels_come_from_the_same_tick_sources(self):
        body = _fn("chartLabels() {", "slippage.html")
        for src in ("this.yTicks()", "this.xTicks()", "this.trendPoints()"):
            assert src in body, "chartLabels stopped reading %s" % src

    def test_screen10_keeps_the_approved_seven_mark_timeline(self):
        """`10. Slippage_Analytics.png` draws 09:15 -> 15:30."""
        code = _code("slippage.html")
        m = re.search(r'XT:\s*\[([^\]]+)\]', code)
        assert m, "XT timeline missing"
        marks = re.findall(r'"([^"]+)"', m.group(1))
        assert marks == ["09:15", "10:00", "11:00", "12:00", "13:00", "14:00", "15:30"], marks

    def test_screen11_labels_come_from_the_shared_geometry(self):
        assert "this.tpGeom()" in _fn("tpLabels() {", "execution.html")
        assert "this.trGeom()" in _fn("trendLabels() {", "execution.html")

    def test_markup_and_labels_share_one_geometry_so_they_cannot_drift(self):
        """⭐ A label in the WRONG PLACE is worse than a small label. Both the
        drawing and the labelling must read the same xf/yf."""
        assert "this.tpGeom()" in _fn("tpMarkup() {", "execution.html")
        assert "this.trGeom()" in _fn("trendMarkup() {", "execution.html")

    def test_the_overlay_divides_by_the_viewbox_the_svg_actually_declares(self):
        """⛔ If a viewBox changes and the constant does not, EVERY label lands
        in the wrong place while still looking plausible."""
        slp = _code("slippage.html")
        m = re.search(r'class="curve-svg slp-curve" viewBox="0 0 (\d+) (\d+)"', slp)
        assert m, "S10 chart viewBox not found"
        assert re.search(r"VBW:\s*%s\b" % m.group(1), slp), "S10 VBW != viewBox width"
        assert re.search(r"VBH:\s*%s\b" % m.group(2), slp), "S10 VBH != viewBox height"

        ex = _code("execution.html")
        mw = re.search(r'class="curve-svg exec-curve-w" viewBox="0 0 (\d+) (\d+)"', ex)
        mt = re.search(r'class="curve-svg exec-curve" viewBox="0 0 (\d+) (\d+)"', ex)
        assert mw and mt, "S11 chart viewBoxes not found"
        assert re.search(r"TP_VBW:\s*%s\b" % mw.group(1), ex), "TP_VBW != viewBox width"
        assert re.search(r"TP_VBH:\s*%s\b" % mw.group(2), ex), "TP_VBH != viewBox height"
        assert re.search(r"TR_VBW:\s*%s\b" % mt.group(1), ex), "TR_VBW != viewBox width"
        assert re.search(r"TR_VBH:\s*%s\b" % mt.group(2), ex), "TR_VBH != viewBox height"

    def test_positions_are_percentages_of_the_viewbox(self):
        for fn, tpl in (("chartLabels() {", "slippage.html"),
                        ("tpLabels() {", "execution.html"),
                        ("trendLabels() {", "execution.html")):
            body = _fn(fn, tpl)
            assert "100 * l.x /" in body and "100 * l.y /" in body, \
                "%s stopped emitting percentage positions" % fn


# ══════════════════════════════════════════════════════════════════════════
# COLLISIONS AND OVERFLOW — both MEASURED defects, both fixed
# ══════════════════════════════════════════════════════════════════════════
class TestNoCollisionNoOverflow:

    def test_throughput_never_prints_the_last_two_x_labels_on_top_of_each_other(self):
        """MEASURED: with 75 buckets the step was 9, so labels landed on 72 and
        the always-show-last rule added 74 — '15:15' and '15:25' overlapped by
        4px at 1920 and 12px at 1440. The END mark is the informative one, so
        the CLASHING stepped label is dropped, ⛔ never both printed."""
        body = _fn("tpLabels() {", "execution.html")
        assert "marks.pop()" in body, "the clash-avoidance was removed"
        assert "Math.ceil(step / 2)" in body, "the minimum-gap rule was removed"

    def test_the_thinning_rule_actually_drops_a_clashing_label(self):
        """Re-implements the rule and checks it on the measured case (75 points,
        max 9 marks) — ⛔ a rule that never fires would be no rule at all."""
        n, xmax = 75, 9
        step = max(1, -(-n // xmax))
        last = n - 1
        marks = list(range(0, last + 1, step))
        if marks[-1] != last:
            if last - marks[-1] < -(-step // 2):
                marks.pop()
            marks.append(last)
        assert last in marks, "the session-end mark must survive"
        gaps = [b - a for a, b in zip(marks, marks[1:])]
        assert min(gaps) >= -(-step // 2), "two marks are still closer than half a step"
        assert 72 not in marks, "the clashing stepped mark should have been dropped"

    def test_screen10_rail_tables_scroll_inside_their_panel(self):
        """MEASURED at 1440: with data, each ranking table is 338px inside a
        306px column and nothing declared overflow-x, so the 32px cascaded to
        `.shell` and the PAGE scrolled (1445 vs 1440). ⚠️ PRE-EXISTING — it was
        invisible only because the earlier check ran with EMPTY tables."""
        rail = _code("slippage.html")
        rail = rail[rail.index('<aside class="slp-rail">'):rail.index("</aside>")]
        assert rail.count('<div class="tbl-scroll">') == 2, \
            "both rail ranking tables must scroll inside their own panel"
        assert rail.count("<table") == 2 and rail.count("</table>") == 2

    def test_no_type_was_shrunk_to_buy_that_width(self):
        """⛔ Fixing overflow by going under the floor would trade one spec
        violation for another."""
        css = _css()
        for scope in (".slp-page", ".exec-page"):
            bad = []
            for m in re.finditer(r"(" + re.escape(scope) + r"[^{\n]*)\{([^}]*)\}", css):
                sel = m.group(1).strip()
                if any(x in sel for x in ("-grip", "-arrow", "-arw")):
                    continue
                fm = re.search(r"font-size:\s*([0-9.]+)px", m.group(2))
                if fm and float(fm.group(1)) < 13:
                    bad.append((sel[:50], fm.group(1)))
            assert not bad, "below the 13px floor: %s" % bad
