"""tests/test_global_table_rule.py — THE DEFERRED GLOBAL TABLE RULE.

📜 Rama, deferred until all 22 screens were built and selected on 02-Sep-2026:
   *"the freeze-pane / body-scroll / header-drag rule is a GLOBAL pass after all
   22 screens are built"*.

WHAT THIS FILE HOLDS, and why each assertion can go red:

  · ONE freeze-pane implementation, ⛔ not five. S11, S18, S19 and S20 each
    carried a byte-identical copy; S20's own comment called itself *"reusing
    S11/S18/S19 rather than a fourth implementation"*, which is precisely the
    duplication a global pass exists to end.
  · The freeze is OPT-IN and reaches only wraps that can actually scroll — a
    sticky header on a table that never scrolls is a z-index and background
    liability for no gain.
  · Every bounded table body opts in. 🔬 This is what found the real defect:
    S17's history table scrolled 12 rows of 498px inside a 188px bound while
    `thead th` computed `position: static`.
  · Draggable headings stay on every cols-driven table, and ⛔ are NOT added to
    tables that carry no column model.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")
_TPL = os.path.join(_ROOT, "frontend", "templates")

#: selector + body for every CSS rule, comments already stripped
_RULE_RE = r"(?:(?<=\})|(?<=\{)|\A)\s*([^{}@\s][^{}]*?)\s*\{([^{}]*)\}"


def _css_no_comments() -> str:
    """⛔ A guard must read the stylesheet, ⛔ never the prose around it — the
    trap this project has been caught by twice."""
    with open(_CSS, encoding="utf-8") as fh:
        return re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.DOTALL)


def _rules() -> list:
    return re.findall(_RULE_RE, _css_no_comments())


def _templates() -> dict:
    out = {}
    for n in os.listdir(_TPL):
        if n.endswith(".html"):
            with open(os.path.join(_TPL, n), encoding="utf-8") as fh:
                out[n] = fh.read()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The freeze pane
# ─────────────────────────────────────────────────────────────────────────────

#: Every CSS rule that bounds a height AND scrolls it, measured 02-Sep-2026.
#: ⭐ A PINNED INVENTORY, ⛔ not a derived sweep — `.tbl-scroll` is a shared class
#: and the rules that bound it are descendant-scoped, so a selector-only sweep
#: cannot tell which occurrence in the markup a given rule reaches. Pinning the
#: set means a NEW bounded wrap fails the test below and its author has to
#: decide, deliberately, whether it holds a table and needs the freeze.
BOUNDED = {
    # selector -> (what it holds, which template owns it)
    ".exec-page .exec-rank-scroll":         ("table",  "execution.html"),
    ".lav-page .lav-feed .lav-tbl-wrap":    ("table",  "live_activity.html"),
    ".sr-page .sr-tbl-wrap":                ("table",  "strategy_ranking.html"),
    ".sh-page .sh-tbl-wrap":                ("table",  "strategy_health.html"),
    ".cfg16-page .cfg16-catscroll":         ("table",  "config.html"),
    ".cfg16-page .cfg16-cmpscroll":         ("table",  "config.html"),
    ".cfg16-page .cfg16-histscroll":        ("table",  "config.html"),
    ".ctl-page .ctl-stratscroll":           ("table",  "controls.html"),
    ".ctl-page .ctl-histpanel .tbl-scroll": ("table",  "controls.html"),
    ".sca-page .sca-am-wrap":               ("table",  "scanner_attribution.html"),
    ".sca-page .sca-map-wrap":              ("table",  "scanner_attribution.html"),
    # bounds a LIST, a legend, a dialog or the app chrome — ⛔ no table, ⛔ no freeze
    ".sidebar":                             ("chrome", None),
    ".ea-panel .ea-list":                   ("list",   None),
    ".ctl-page .ctl-hist":                  ("list",   None),
    ".pnl-page .pnl-legend":                ("list",   None),
    ".pos-page .pos-modal-box":             ("dialog", None),
    ".slg-page .slg-replay-list":           ("list",   None),
    ".tex-page .tex-legend":                ("list",   None),
    ".tlg-page .tlg-raw":                   ("list",   None),
}

#: The EXACT class attribute of every element that opts into the freeze, and how
#: many of each its template holds. 🔬 Fifteen wraps across seven screens,
#: measured 02-Sep-2026.
#: ⚠️ PINNED AS FULL SIGNATURES, ⛔ not as "does this class appear somewhere".
#: A mutation proved the looser form vacuous TWICE: `controls.html` carries two
#: `tbl-scroll` wraps, so asking whether ANY of them was frozen stayed green
#: while the history table — the one real defect this build fixes — lost its
#: freeze. A signature naming the whole class list cannot be satisfied by a
#: sibling.
FROZEN_MARKUP = {
    "execution.html":           {"exec-rank-scroll tbl-freeze": 3},
    "live_activity.html":       {"lav-tbl-wrap tbl-freeze": 3},
    "strategy_ranking.html":    {"sr-tbl-wrap tbl-freeze": 1},
    "strategy_health.html":     {"sh-tbl-wrap tbl-freeze": 1},
    "config.html":              {"tbl-scroll cfg16-catscroll tbl-freeze": 1,
                                 "tbl-scroll cfg16-cmpscroll tbl-freeze": 1,
                                 "tbl-scroll cfg16-histscroll tbl-freeze": 1},
    # ⭐ the second entry is S17's HISTORY table — the only table in the GUI
    # whose body scrolled while its header scrolled away.
    "controls.html":            {"tbl-scroll ctl-stratscroll tbl-freeze": 1,
                                 "tbl-scroll tbl-freeze": 1},
    "scanner_attribution.html": {"sca-am-wrap tbl-freeze": 1,
                                 "sca-map-wrap tbl-freeze": 1},
}

#: ⭐ Named because it bounds the TABLE ELEMENT inside an already-frozen wrap,
#: ⛔ not a second scroller: `.exec-rank-scroll table { width: 100% }` sits in
#: the same block and the sweep sees the pair.
_INSIDE_A_FROZEN_WRAP = {".exec-page .exec-rank-scroll table"}


class TestFreezePane:

    def test_there_is_exactly_one_freeze_pane_implementation(self) -> None:
        """⭐ THE POINT OF THE GLOBAL PASS: four identical copies become one."""
        sticky = [sel.strip() for sel, body in _rules()
                  if "thead" in sel and "position: sticky" in body]
        assert sticky == [".tbl-freeze thead th"], sticky

    def test_the_shared_rule_declares_the_whole_pattern(self) -> None:
        """⛔ Sticky alone is not the pattern. Without a background the rows show
        through the header as they pass under it; `top: 0` is relative to the
        SCROLLING WRAP, so the page's own scroll can never move it."""
        body = [b for sel, b in _rules() if sel.strip() == ".tbl-freeze thead th"][0]
        for decl in ("position: sticky", "top: 0", "z-index", "background:"):
            assert decl in body, (decl, body)

    def test_the_bounded_scrolling_set_has_not_changed(self) -> None:
        """⭐ THE GUARD THAT KEEPS THE RULE HONEST OVER TIME. A frozen header is
        only required where a body can scroll, so the set of bounding rules IS
        the rule's scope. A new one fails here and must be classified — ⛔ rather
        than the freeze silently not reaching it."""
        found = {sel.strip() for sel, body in _rules()
                 if re.search(r"(max-)?height:\s*\d", body)
                 and re.search(r"overflow(-y)?:\s*(auto|scroll)", body)}
        unclassified = found - set(BOUNDED) - _INSIDE_A_FROZEN_WRAP
        assert not unclassified, ("new bounded scroller — classify it in BOUNDED",
                                  sorted(unclassified))

    def test_every_bounded_table_wrap_opts_into_the_freeze(self) -> None:
        """⛔ The half that matters: a bounded wrap holding a TABLE must carry
        `tbl-freeze`, or its header scrolls away. 🔬 S17's history table is the
        instance this caught on 02-Sep.

        ⚠️ CHECKED IN THE OWNING TEMPLATE, ⛔ not across all markup. A first
        draft asked only whether SOME element anywhere carried the pair, and a
        mutation proved it vacuous: removing the freeze from S17's history wrap
        left the test green, because S16's three `tbl-scroll tbl-freeze` wraps
        satisfied it.
        """
        tpls = _templates()
        for owner, sigs in FROZEN_MARKUP.items():
            src = tpls[owner]
            for sig, n in sigs.items():
                got = src.count('class="%s"' % sig)
                assert got == n, (owner, sig, "expected %d, found %d" % (n, got))
        # ⭐ and nothing else in the GUI claims the freeze
        total = sum(n for sigs in FROZEN_MARKUP.values() for n in sigs.values())
        seen = sum(len(re.findall(r'\btbl-freeze\b', s)) for s in tpls.values())
        assert seen == total, ("a tbl-freeze appeared outside the pinned set",
                               seen, total)

    def test_the_freeze_is_opt_in_and_never_blanket(self) -> None:
        """⛔ NOT APPLIED TO EVERY TABLE. 🔬 Of 76 tables measured across the 22
        screens on 02-Sep, exactly 13 have a bounded or scrolling body; the other
        63 fit their content and get nothing."""
        for sel, body in _rules():
            if "position: sticky" not in body or "thead" not in sel:
                continue
            s = sel.strip()
            assert s.startswith("."), ("a bare element selector would freeze every "
                                       "table on every screen", s)
            assert "tbl-freeze" in s, s

    def test_a_wrap_that_opts_in_is_one_that_actually_scrolls(self) -> None:
        """A freeze with nothing to scroll under it is decoration. Every
        `tbl-freeze` element carries a class that some rule bounds and scrolls."""
        bounded_classes = {sel.split()[-1].lstrip(".") for sel in BOUNDED}
        for name, src in _templates().items():
            for m in re.finditer(r'class="([^"]*\btbl-freeze\b[^"]*)"', src):
                classes = {c for c in m.group(1).split() if c != "tbl-freeze"}
                assert classes & bounded_classes, (name, m.group(1),
                                                   "opts into the freeze but never scrolls")


# ─────────────────────────────────────────────────────────────────────────────
# Header drag
# ─────────────────────────────────────────────────────────────────────────────
class TestHeaderDrag:

    def test_every_cols_driven_table_keeps_its_draggable_headings(self) -> None:
        """🔬 Measured 02-Sep: 16 templates render their table from a `cols`
        array and all 16 use the shared `colDragMixin`. A cols-driven table
        without it is a silent loss of column reordering."""
        missing = [n for n, s in _templates().items()
                   if re.search(r"\bcols:\s*\[", s) and "colDragMixin" not in s]
        assert not missing, missing

    def test_drag_is_not_bolted_onto_tables_that_cannot_reorder(self) -> None:
        """📜 Rama, 02-Sep: *"Do not add drag behaviour to static/non-reorderable
        tables merely to make the count 22/22."* A table whose `<th>`s are
        hand-written has no column model, so a grip would be a control that does
        nothing."""
        for name, src in _templates().items():
            if "colDragMixin" in src:
                assert re.search(r"\bcols\b", src), (name, "drag without a column model")

    def test_no_screen_re_implements_a_mixin_handler(self) -> None:
        """⭐ The mixin is the single owner.
        ⚠️ A DEFINITION, ⛔ not a call site — `@dragend="onDragEnd()"` is the
        markup CALLING the shared handler and must stay; only a re-declared
        method body is a second implementation."""
        for name, src in _templates().items():
            if "colDragMixin" not in src:
                continue
            for pat in (r"onDragStart\s*\(\s*key\s*,\s*ev\s*\)\s*\{",
                        r"onDrop\s*\(\s*targetKey\s*\)\s*\{",
                        r"onDragEnd\s*\(\s*\)\s*\{"):
                assert not re.search(pat, src), (name, pat, "re-implements a handler")


# ─────────────────────────────────────────────────────────────────────────────
# Header alignment — the other half of the 14-Aug column-role spec
# ─────────────────────────────────────────────────────────────────────────────
class TestHeaderFollowsItsColumn:

    def test_the_header_rule_mirrors_the_data_rule_it_pairs_with(self) -> None:
        """📜 14-Aug column-role spec: *"data column HEADINGS …. CENTER (over
        their own data)"*. The data rule centres the cell; this centres the
        heading that names it. ⭐ Both key on the SAME markers, so a column
        cannot be data for one rule and not the other."""
        css = _css_no_comments()
        data = re.search(r"((?:table td\.[a-z-]+,\s*)+table td\.[a-z-]+ \{[^}]*\})", css).group(1)
        head = re.search(r"((?:table th\.[a-z-]+,\s*)+table th\.[a-z-]+ \{[^}]*\})", css).group(1)
        markers = lambda block: sorted(re.findall(r"\.([a-z-]+) ?\{|\.([a-z-]+),", block))
        d = sorted(set(re.findall(r"td\.([a-z-]+)", data)))
        h = sorted(set(re.findall(r"th\.([a-z-]+)", head)))
        assert d == h, (d, h)
        assert "center" in head and "!important" in head, head

    def test_the_header_rule_never_targets_a_bare_th(self) -> None:
        """⛔ A bare `th` would move every LABEL heading in the GUI. The rule is
        only allowed to reach a heading the codebase has already marked as
        sitting over data."""
        for sel, body in _rules():
            if "text-align: center" not in body or not sel.strip().startswith("table th"):
                continue
            for part in sel.split(","):
                last = part.strip().split()[-1]
                assert "." in last, ("the GLOBAL header rule reached a bare th", sel)
        # ⚠️ SCOPED TO THE GLOBAL RULE ON PURPOSE. A first draft swept every
        # centred selector and went red on `.ord-page .ord-grp th` — S05's
        # group-band header row, deliberately centred across its span long before
        # this pass. ⛔ That is a screen-specific approved decision, not a
        # violation, and a guard that cannot tell them apart is the wrong guard.

    def test_the_deliberate_per_column_hc_flags_survive(self) -> None:
        """⛔ THE EXCEPTION THIS RULE MUST NOT OVERTURN. S04, S05 and S06 carry
        an explicit per-column `hc: true` that centres a heading over a
        LEFT-aligned badge column — an approved decision from an earlier pass.
        🔬 14 columns. The global rule keys on data markers, which those columns
        do not carry, so it cannot reach them."""
        tpls = _templates()
        counts = {n: len(re.findall(r"\bhc:\s*true", s)) for n, s in tpls.items()}
        assert counts.get("signals.html") == 10, counts.get("signals.html")
        assert counts.get("orders.html") == 14, counts.get("orders.html")
        assert counts.get("positions.html") == 19, counts.get("positions.html")
        css = _css_no_comments()
        for scope in (".sig-page", ".ord-page", ".pos-page"):
            assert re.search(re.escape(scope) + r"[^{]*th\.hc \{[^}]*text-align: center",
                             css), scope
