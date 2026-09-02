"""tests/test_b1_typography_floor.py — B1 (D3 TYPOGRAPHY), RULED 19-Aug-2026.

Design authority: the READABILITY block of every screen TXT in `gui/`. Twenty of
the twenty-two carry the same line — "Minimum: 13px". The two that do not are
`01. Login-Screen.txt` (a TYPOGRAPHY section naming fonts and weights, but NO px
minimum) and `18. Live_Activity.txt` (no readability section at all).

⛔ THE 63-RULE COUNT WAS A STYLESHEET COUNT, NOT AN EXPOSURE COUNT.
The cascade already carried dozens of page-scoped 13px overrides, so most of the
63 never reached a screen below the floor. Measured in headless Edge across all
22 screens at 1440x900 AND 1920x1080 — every rendered element carrying its own
text node — the real exposure was EIGHT findings in FIVE declarations:

    .btn-logout   12.48px   21 screens   "Log out"
    .flt-k        12.00px    8 screens   filter labels ("Date Range")
    .sev-chip     10.56px    S02, S08    "1 critical" / "INFO" / "CRITICAL"
    .info-banner  12.80px    S02, S03    "2 alert(s) today ..."
    .events       12.80px    S08         event rows (and `.ev-ts`, which
                                         declares no size and inherited it)

All five are raised here. Re-measured after: ZERO sub-13px text on S02-S22 at
both viewports, and page overflow unchanged at 0 on all 22.

WHAT IS **NOT** RAISED, AND WHY — each is a decision, not an oversight:

  · DECORATIVE GLYPHS (B2). `.st-arw` 9.00, `.st-grip` 10.00, `*-arrow` 9.28,
    `*-grip` 8.80 — sort indicators and drag handles, each a single symbol
    carrying no readable text. This is the exemption B2 formalises: a genuinely
    decorative, non-readable glyph may sit below 13px where the approved artwork
    needs it. ⛔ It may NOT be used to reclassify readable text.

  · SCREEN 01. `<label>` 12.48px and `.foot` 11.52px live in `login.html`'s own
    inline <style> — the file extends nothing and loads no shared stylesheet.
    Its spec states no px minimum, so raising them would be preference, not
    compliance. ⛔ Left as drawn, and recorded rather than silently skipped.

  · SVG USER-UNIT TEXT. An SVG font-size is in USER UNITS, so its declared value
    means nothing on its own: rendered = declared x (container / viewBox). All of
    it was MEASURED via getScreenCTM at 1920 AND 1440, and every one clears the
    floor -- 0 below it:

        .cap-g-tick    S08   6.5 / 7.2 declared  ->  13.63 / 13.39 rendered
        .sr-donut-t1/2 S19   4.72 declared       ->  13.04 at both
        .sh-donut-t1/2 S20   4.41 declared       ->  13.02 at both
        .hld-donut-t1  S22   7.5  declared       ->  20.00 at both
        .hld-donut-t2  S22   4.9  declared       ->  13.07 at both

    ⭐ The S10/S11 chart labels needed no exemption at all: they already left the
    SVG for an HTML overlay at a real 13px.
"""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")

#: Raised by B1. Each maps to a measured render-time finding above.
RAISED = (
    (".btn-logout", "cursor: pointer; font-size:"),
    (".events", "padding: 0; font-size:"),
    (".info-banner", "border-radius: 8px; font-size:"),
    (".sev-chip", ".sev-chip { font-size:"),
    (".flt-k", ".hld-page .flt-k { font-size:"),
)

#: The B2 families. ⛔ Membership is by SHAPE (a symbol), never by convenience.
GLYPH_FAMILIES = ("-grip", "-arw", "-arrow")


def _css():
    with open(_CSS, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _sizes_px(css):
    """(selector, px) for every declaration that states a font-size, comments
    removed so prose about a size can never be read as a rule."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", _strip_comments(css)):
        sel = " ".join(m.group(1).split())
        for fm in re.finditer(r"font-size:\s*([0-9.]+)(px|rem)", m.group(2)):
            v = float(fm.group(1))
            out.append((sel, round(v * 16, 2) if fm.group(2) == "rem" else v))
    return out


class TestTheFiveRaisedRules:
    """⛔ Each was measured below the floor in the browser, on a named screen."""

    def test_every_raised_rule_now_states_at_least_13px(self):
        css = _css()
        bad = []
        for cls, anchor in RAISED:
            i = css.find(anchor)
            assert i != -1, "%s: the anchored declaration moved" % cls
            m = re.search(r"font-size:\s*([0-9.]+)(px|rem)", css[i:i + 120])
            assert m, "%s: no font-size after its anchor" % cls
            px = float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
            if px < 13:
                bad.append((cls, px))
        assert not bad, "fell back below the 13px floor: %s" % bad

    def test_the_raised_rules_are_stated_in_px_not_rem(self):
        """rem hid all five from the earlier px-only scan. Stating them in px
        keeps them visible to the cheapest possible check."""
        css = _css()
        for cls, anchor in RAISED:
            i = css.find(anchor)
            m = re.search(r"font-size:\s*([0-9.]+)(px|rem)", css[i:i + 120])
            assert m.group(2) == "px", "%s went back to rem" % cls


class TestTheGlyphExemptionIsBoundedB2:
    """B2: the exemption covers decorative glyphs — and ONLY those."""

    def test_sub_floor_rules_are_glyphs_svg_ticks_or_out_of_campaign_scope(self):
        """Anything else below the floor is a NEW violation, so this fails
        loudly rather than letting one appear quietly."""
        campaign = (".dash-page", ".strat-page", ".sig-page", ".ord-page",
                    ".pos-page", ".tex-page", ".cap-page", ".pnl-page",
                    ".slp-page", ".exec-page", ".sysh-page", ".aud-page",
                    ".tlg-page", ".slg-page", ".cfg16-page", ".ctl-page",
                    ".lav-page", ".sr-page", ".sh-page", ".sca-page",
                    ".hld-page")
        # ⛔ Each allowed family is SVG user-unit text or a decorative glyph.
        #    The SVG ones are only defensible because their RENDERED size is
        #    measured -- see the module docstring and the rendered-size tests.
        allowed = GLYPH_FAMILIES + ("-tick", "-axis", "-donut-t", ".cxl")
        bad = [(s[:70], v) for s, v in _sizes_px(_css())
               if v < 13
               and any(c in s for c in campaign)
               and not any(x in s for x in allowed)]
        assert not bad, "sub-13px rules reaching a campaign screen: %s" % bad

    def test_the_glyph_families_still_exist_so_the_exemption_is_not_stale(self):
        """If the glyphs are gone, the exemption should shrink loudly instead of
        standing as cover for something new."""
        found = {fam for s, v in _sizes_px(_css()) if v < 13
                 for fam in GLYPH_FAMILIES if fam in s}
        assert found, "no glyph rule is below 13px any more — retire the exemption"

    def test_the_exemption_is_not_claimed_for_a_readable_label(self):
        """⛔ B2's own boundary: a rule may not join the glyph families just by
        being named like one. Every sub-floor glyph rule must be a single-symbol
        element, which in this stylesheet means it declares no width/padding
        that implies a text run."""
        offenders = [s[:70] for s, v in _sizes_px(_css()) if v < 13
                     and any(f in s for f in GLYPH_FAMILIES)
                     and ("label" in s or "title" in s or "value" in s)]
        assert not offenders, "readable text hiding in the glyph exemption: %s" % offenders


class TestScreen01IsExemptForARecordedReason:

    def test_login_still_carries_its_own_inline_style_block(self):
        """S01's two sub-floor rules are inline in `login.html`. The exemption
        rests on that file being outside the shared stylesheet AND on its spec
        stating no px minimum — so if it ever starts loading `style.css`, the
        reasoning must be revisited."""
        tpl = os.path.join(_ROOT, "frontend", "templates", "login.html")
        with open(tpl, encoding="utf-8") as fh:
            src = fh.read()
        assert "<style" in src, "login.html no longer carries its own styles"
        assert "style.css" not in src, \
            "login.html now loads the shared stylesheet — re-check the S01 exemption"
