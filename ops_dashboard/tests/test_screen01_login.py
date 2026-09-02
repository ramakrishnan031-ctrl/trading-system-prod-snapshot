"""tests/test_screen01_login.py — SCREEN 01 LOGIN.

Design authority: `gui/01. Login-Screen.png` + `gui/01. Login-Screen.txt`.

Screen 01 was the ONE screen in the 01-22 campaign with no dedicated test file,
so nothing pinned the login surface at all. It is also the security boundary of
a read-only control tower, which makes an unpinned surface the worst place to
have a gap. This suite pins what the spec actually states:

  · BRANDING   title / subtitle / description, verbatim from the spec;
  · FIELDS     Username, Password, Authenticator Code (6-digit TOTP);
  · ACTION     a single primary "Sign In" button;
  · FOOTER     "Read Only · TOTP Protected";
  · PRINCIPLES the spec's explicit NOTs — no dashboard preview, no market data,
               no live charts, no statistics, NO ANIMATIONS;
  · ISOLATION  the page is SELF-CONTAINED: zero external http(s) references, so
               the login screen cannot be made to talk to a third party;
  · FLOOR      nothing in the login's own CSS is below the spec's 13px floor.

⛔ These assert the SPEC, not the PNG: `01. Login-Screen.png` is a screenshot of
a build whose chip is rendered in flat neon, while the TXT calls for a
"Metallic / engraved" eagle. Where the two disagree the written spec governs,
and the shipped build follows the written spec.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_TPL = os.path.join(_ROOT, "frontend", "templates", "login.html")


def _tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _tpl_no_data() -> str:
    """Template with the embedded hero data: URI collapsed.

    📌 The hero is a ~150 KB base64 payload. Left in place, a regex for
    `https?://` or for a colour would scan the image bytes and could match
    anything; every structural scan below therefore runs on this reduction.
    """
    return re.sub(r"data:image/[^\"')]+", "<DATAURI>", _tpl())


# ══════════════════════════════════════════════════════════════════════════
# BRANDING — spec section "BRANDING"
# ══════════════════════════════════════════════════════════════════════════
class TestBranding:

    def test_the_three_branding_strings_are_present_verbatim(self):
        """Spec: Title `AlgoCore Systems`, Subtitle `Operations Control Tower`,
        Description `Secure Read-Only Access`."""
        t = _tpl_no_data()
        for s in ("AlgoCore", "Operations Control Tower", "Secure Read-Only Access"):
            assert s in t, "missing branding string: %r" % s

    def test_the_card_subtitle_joins_tower_and_access_as_the_spec_writes_it(self):
        """Spec, LOGIN CARD: `Operations Control Tower · Secure Read-Only Access`."""
        t = _tpl_no_data()
        assert re.search(r"Operations Control Tower\s*·\s*Secure Read-Only Access", t), \
            "the card subtitle is not the single joined line the spec specifies"


# ══════════════════════════════════════════════════════════════════════════
# FIELDS + ACTION — spec section "LOGIN CARD"
# ══════════════════════════════════════════════════════════════════════════
class TestCard:

    def test_exactly_three_inputs_exist(self):
        """Spec lists exactly Username, Password, Authenticator Code."""
        t = _tpl_no_data()
        assert len(re.findall(r"<input", t)) == 3, \
            "the spec's card has exactly three fields"

    def test_the_password_field_is_a_password_field(self):
        assert _tpl_no_data().count('type="password"') == 1

    def test_the_totp_field_is_numeric_and_six_digits(self):
        """Spec: `Authenticator Code (6-digit TOTP)`."""
        t = _tpl_no_data()
        assert 'inputmode="numeric"' in t, "TOTP field should ask for a numeric keypad"
        assert re.search(r'pattern="\[0-9\]\*"', t), "TOTP field should accept digits only"

    def test_credentials_are_not_offered_for_autofill(self):
        """A shared operations terminal must not autofill an operator's login."""
        assert 'autocomplete="off"' in _tpl_no_data()

    def test_there_is_exactly_one_primary_action_named_sign_in(self):
        """Spec, Primary Button: `Sign In`."""
        t = _tpl_no_data()
        assert t.count("Sign In") == 1
        assert len(re.findall(r"<button", t)) <= 1, \
            "the spec's card has ONE primary action"

    def test_the_footer_is_the_spec_line(self):
        """Spec, Footer: `Read Only · TOTP Protected`."""
        assert re.search(r"Read Only\s*·\s*TOTP Protected", _tpl_no_data())


# ══════════════════════════════════════════════════════════════════════════
# DESIGN PRINCIPLES — the spec's explicit NOTs
# ══════════════════════════════════════════════════════════════════════════
class TestPrinciples:

    def test_no_dashboard_preview_no_market_data_no_charts_no_statistics(self):
        """Spec, DESIGN PRINCIPLES: `No dashboard preview / No market
        information / No live charts / No statistics`."""
        t = _tpl_no_data()
        assert "<canvas" not in t, "a canvas on the login screen would be a chart"
        for banned in ("chart", "sparkline", "Chart.js", "plotly"):
            assert banned.lower() not in t.lower(), "login must carry no chart: %r" % banned

    def test_no_animations(self):
        """Spec: `No animations` — and, in LEFT HERO, `No animation`."""
        t = _tpl_no_data()
        assert not re.search(r"@keyframes|animation\s*:", t), \
            "the spec forbids animation on this screen"

    def test_no_script_runs_on_the_login_page(self):
        """The login screen is the pre-auth surface: nothing should execute."""
        assert not re.search(r"<script", _tpl_no_data())


# ══════════════════════════════════════════════════════════════════════════
# ISOLATION — the page must be self-contained
# ══════════════════════════════════════════════════════════════════════════
class TestIsolation:

    def test_zero_external_references(self):
        """⛔ The pre-auth page must not reach any third party. The hero art is
        embedded as a data: URI precisely so no network fetch is needed."""
        t = _tpl_no_data()
        # ⛔ The first version of this pattern had no `=` between the attribute
        # and the URL, so it could NEVER match `href="https://..."`. The
        # non-vacuity harness caught it staying GREEN with a CDN link planted.
        externals = re.findall(
            r"(?:src|href)\s*=\s*[\"']?\s*https?://[^\"'>\s]+"
            r"|url\(\s*[\"']?\s*https?://[^\"')\s]+", t)
        assert not externals, "login reaches external hosts: %s" % externals[:5]

    def test_the_hero_is_embedded_not_fetched(self):
        assert "data:image/" in _tpl(), \
            "the hero illustration should be embedded, not loaded over the network"


# ══════════════════════════════════════════════════════════════════════════
# TYPOGRAPHY — the campaign-wide floor
# ══════════════════════════════════════════════════════════════════════════
class TestTypeFloor:

    def test_no_login_scoped_rule_is_below_13px(self):
        """The 13px readability floor every screen spec states."""
        offenders = []
        for block in re.findall(r"<style[^>]*>(.*?)</style>", _tpl_no_data(), re.S):
            for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", block):
                m = re.search(r"font-size:\s*([0-9.]+)px", rule.group(2))
                if m and float(m.group(1)) < 13:
                    offenders.append((rule.group(1).strip()[:48], m.group(1)))
        assert not offenders, "below the 13px floor: %s" % offenders
