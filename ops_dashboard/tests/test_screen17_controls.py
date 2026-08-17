"""tests/test_screen17_controls.py — SCREEN 17 CONTROLS.

📜 Rama, 17-Aug-2026: Screen 17 is the FULL operational control surface; L4's
   read-only interpretation is superseded. Screen 16 stays informational.
🖼️ Visual authority: gui/17. Controls.png
📜 Design authority: docs/decisions/CONTROL_PLANE_DESIGN_17-Aug-2026.md

⭐ The theme of this file: **a control may never look applied unless the running
system said it was.** Most assertions below exist to make a fake success
impossible rather than to check that a route returns 200.
"""
from __future__ import annotations

import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_TPL = os.path.join(_ROOT, "frontend", "templates", "controls.html")
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")


def _tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _markup() -> str:
    """The template with Jinja `{# … #}` and HTML `<!-- … -->` comments removed.

    📌 RULE, learned the hard way FOUR times in one session: a source-scanning
    test must scan what RENDERS, not what is written about it. This template's
    own comments explain that Scanner Controls was removed — a raw substring
    search therefore finds the words it is asserting are absent, and the test
    fails on its own documentation.
    """
    src = _tpl()
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.DOTALL)
    return src


# ─────────────────────────────────────────────────────────────────────────────
# The endpoint contract
# ─────────────────────────────────────────────────────────────────────────────
class TestScreenEndpoint:

    def test_screen_payload_has_every_block_the_artwork_needs(self, client) -> None:
        d = client.get("/api/controls/screen").get_json()
        for key in ("control_plane", "trading_mode", "strategies", "alerts",
                    "limits", "market_protection", "readiness",
                    "active_controls_summary", "control_history",
                    "simulation_mode", "config_view"):
            assert key in d, f"missing block: {key}"

    def test_the_existing_controls_summary_contract_is_untouched(self, client) -> None:
        """⛔ ADDITIVE ONLY. The old endpoint keeps its exact shape — other
        callers and its own contract test depend on it."""
        d = client.get("/api/controls-summary").get_json()
        assert d["read_only"] is True
        for key in ("trade_type", "strategies", "limits", "kill_switch",
                    "telegram_alerts", "future_controls"):
            assert key in d

    def test_control_plane_reports_unavailable_when_no_trader_is_running(self, client) -> None:
        """🔑 In the test environment nothing is listening on the control port, so
        the screen MUST say controls are unavailable. If this ever reported
        `available: True` with no trader, the screen would be lying."""
        d = client.get("/api/controls/screen").get_json()
        assert d["control_plane"]["available"] is False
        assert d["control_plane"]["reason"]

    def test_strategy_rows_separate_configured_from_live(self, client) -> None:
        """⛔ A YAML value is what the NEXT boot will do; it is not runtime truth.
        The payload must keep them apart."""
        rows = client.get("/api/controls/screen").get_json()["strategies"]["rows"]
        assert rows, "fixture must supply strategies or this test is vacuous"
        for r in rows:
            assert "configured_enabled" in r and "live_enabled" in r and "diverged" in r

    def test_no_separate_scanner_control_identity_is_produced(self, client) -> None:
        """⭐ scanner = strategy (1:1). A second identity would print one thing
        twice — the defect Screen 21 was corrected for."""
        d = client.get("/api/controls/screen").get_json()
        assert "scanners" not in d
        assert "scanner_controls" not in d


# ─────────────────────────────────────────────────────────────────────────────
# The action endpoint — the part that could fake a success
# ─────────────────────────────────────────────────────────────────────────────
class TestActionEndpoint:

    def test_unknown_action_is_refused(self, client) -> None:
        r = client.post("/api/controls/action", json={"action": "rm -rf"})
        assert r.status_code == 400

    def test_action_is_an_allowlist_not_a_path_passthrough(self, client) -> None:
        """⛔ A proxy that forwards arbitrary paths to a plane that can halt
        trading is an open relay onto the kill switch."""
        for evil in ("/control/full-stop", "../control/full-stop",
                     "http://evil/control", "control/full-stop"):
            r = client.post("/api/controls/action", json={"action": evil})
            assert r.status_code == 400, f"path {evil!r} was not refused"

    def test_unreachable_plane_is_503_and_explicitly_not_applied(self, client) -> None:
        """🔑 THE ANTI-FAKE-SUCCESS TEST. Nothing is listening, so the response
        must say applied=False and reachable=False — ⛔ never a cheerful 200."""
        r = client.post("/api/controls/action",
                        json={"action": "entries.pause", "payload": {}})
        assert r.status_code == 503
        b = r.get_json()
        assert b["ok"] is False and b["applied"] is False and b["reachable"] is False
        assert b["error"]

    def test_every_allowlisted_action_is_reachable_by_name(self, client) -> None:
        """Anti-vacuity for the allowlist: each known action must get PAST the
        400 (and fail at the transport instead), or the list is decorative."""
        for action in ("entries.pause", "entries.resume", "trading.stop",
                       "strategy.toggle", "limits.update"):
            r = client.post("/api/controls/action",
                            json={"action": action, "payload": {}})
            assert r.status_code != 400, f"{action} was rejected as unknown"


# ─────────────────────────────────────────────────────────────────────────────
# The template — artwork fidelity and honesty
# ─────────────────────────────────────────────────────────────────────────────
class TestTemplateMatchesTheArtwork:

    REQUIRED_HEADINGS = (
        "TRADING MODE CONTROLS", "STRATEGY CONTROLS", "ALERT CONTROLS",
        "RUNTIME LIMIT CONTROLS", "MARKET PROTECTION CONTROLS",
        "READINESS CHECK", "CHANGE PREVIEW", "CONFIRM CHANGE",
        "BROKER COSTS", "CONFIGURATION COMPARISON", "HISTORY",
        "ACTIVE CONTROLS SUMMARY", "CONTROL HISTORY", "SIMULATION MODE",
        "APPLY CONTROLS", "SEARCH CONTROLS",
    )

    def test_every_artwork_panel_is_present(self) -> None:
        src = _tpl()
        missing = [h for h in self.REQUIRED_HEADINGS if h not in src]
        assert not missing, f"artwork panels missing from Screen 17: {missing}"

    def test_the_four_market_protection_actions_are_all_present_and_distinct(self) -> None:
        """⛔ They must stay visually distinct and unambiguous — they are safety
        controls with different blast radii."""
        src = _tpl()
        for label in ("Pause New Entries", "Allow Existing Position Management",
                      "Allow Exits Only", "Full Trading Stop"):
            assert label in src, f"missing protection control: {label}"

    def test_full_stop_and_pause_have_different_confirmation_weight(self) -> None:
        """🔑 R1: Full Trading Stop is materially different from Pause and the UI
        must not let them look equivalent."""
        src = _tpl()
        assert "FULL TRADING STOP" in src, "the typed confirmation phrase must appear"
        assert "deploy/resume.sh" in src, "the recovery cost must be stated to the operator"
        assert "NOT the same as Pause New Entries" in src
        # Pause has no typed confirmation — that asymmetry IS the requirement.
        assert "askFullStop()" in src and "pauseEntries()" in src

    def test_no_scanner_controls_panel(self) -> None:
        """⭐ Removed by the scanner=strategy decision, ⛔ not by omission.

        Checked on MARKUP: the template's own comment explains the removal, so a
        raw search would match the very words it asserts are absent.
        """
        assert "SCANNER CONTROLS" not in _markup(), "a Scanner Controls panel renders"
        # …and the decision itself must stay recorded in the file.
        assert "scanner = strategy" in _tpl().lower()

    def test_controls_are_disabled_when_the_plane_is_down(self) -> None:
        src = _tpl()
        assert src.count(':disabled="!planeUp()') >= 4, (
            "every operable control must disable when the control plane is unreachable"
        )

    def test_the_screen_renders_returned_state_not_requested_state(self) -> None:
        """⛔ After every action the screen re-reads the runtime — including
        after a failure."""
        src = _tpl()
        assert "await this.load();" in src
        assert "finally {" in src, "the re-read must happen even when the action throws"

    def test_no_chart_is_invented(self) -> None:
        """The Screen 17 artwork contains NO chart. The rule cuts both ways: do
        not remove one that exists, and ⛔ do not invent one that does not."""
        src = _tpl().lower()
        for tag in ("<svg", "<canvas", "donut", "sparkline", "chart("):
            assert tag not in src, f"Screen 17 has no chart in the artwork; found {tag!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Visual language
# ─────────────────────────────────────────────────────────────────────────────
class TestVisualLanguage:

    def _css_window(self) -> str:
        """Screen 17's CSS block ONLY.

        📌 Bounded at the next screen header — a window that runs to EOF silently
        adopts whatever is appended after it (the defect Screens 12/18/20 were
        corrected for).
        """
        with open(_CSS, encoding="utf-8") as fh:
            css = fh.read()
        start = css.index("SCREEN 17 — CONTROLS")
        nxt = css.find("\n   SCREEN ", start + 10)
        return css[start:] if nxt == -1 else css[start:nxt]

    def test_every_rule_is_scoped_to_the_page(self) -> None:
        win = self._css_window()
        rules = re.findall(r"^(\.[a-zA-Z][^\s{,]*)", win, re.MULTILINE)
        unscoped = [r for r in rules if not r.startswith(".ctl-page")]
        assert not unscoped, f"unscoped Screen-17 rules would leak: {unscoped}"

    def test_no_font_size_below_the_13px_floor(self) -> None:
        win = self._css_window()
        px = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)px", win)]
        assert px, "no font sizes found — the window is probably wrong"
        assert min(px) >= 13, f"below the 13px readability floor: {sorted(px)[:4]}"

    def test_no_new_colour_is_introduced(self) -> None:
        """⛔ Every colour must be an existing token; a raw hex here would be a
        new colour in an approved design language."""
        win = self._css_window()
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", win)
        assert not [h for h in hexes if h.lower() != "#fff"], (
            f"raw colours introduced: {hexes}"
        )

    def test_enabled_disabled_use_the_artwork_semantics(self) -> None:
        win = self._css_window()
        assert "--green" in win and "--red" in win
        assert ".ctl-sw-on" in win and ".ctl-sw-off" in win
