"""tests/test_screen16_config.py — SCREEN 16 CONFIGURATION.

The approved artwork is `gui/16. Configuration.png`. Screen 16 is the
INFORMATION BOARD: it shows the active configuration, a comparison and a
history, and it exposes NO operational control. Screen 17 owns every action.

What these tests hold, and why each one can go red:

  · the payload carries every block the artwork draws;
  · ⛔ NOT ONE artwork number is hard-coded — the values come from the running
    configuration, and the artwork's mock numbers are asserted ABSENT;
  · ⛔ no scanner identity: no Scanner column, no SCANNER MAPPING panel, no
    per-scanner row — scanner and strategy are 1:1;
  · ⛔ no control: no mutating verb, no toggle, no Apply/Cancel/Revert, and both
    new routes accept GET only;
  · an unavailable value renders as unavailable and is never substituted;
  · "Changed By" is never fabricated;
  · the old `/api/config` contract is untouched.
"""
from __future__ import annotations

import io
import os
import re

import yaml

import _js_syntax

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_TPL = os.path.join(_ROOT, "frontend", "templates", "config.html")
_CSS = os.path.join(_ROOT, "frontend", "static", "style.css")

from backend.services import config_view  # noqa: E402


def _tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _markup() -> str:
    """The template with Jinja `{# … #}` and HTML comments removed.

    📌 A source-scanning test must scan what RENDERS, not what is written about
    it. This template's own comments explain that the Scanner column and the
    SCANNER MAPPING panel were removed — a raw substring search would find the
    very words it asserts are absent and fail on the documentation.
    """
    src = _tpl()
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.DOTALL)
    return src


def _css_block() -> str:
    """Screen 16's OWN block of style.css, bounded at the next screen header."""
    with open(_CSS, encoding="utf-8") as fh:
        css = fh.read()
    start = css.index("SCREEN 16 — CONFIGURATION")
    nxt = css.find("\n   SCREEN ", start + 10)
    return css[start:] if nxt == -1 else css[start:nxt]


def _css_rules() -> str:
    """Screen 16's CSS block with every `/* … */` comment REMOVED.

    📌 THE SAME RULE `_markup()` STATES FOR THE TEMPLATE, and it was earned the
    same way: `test_nothing_is_hidden_at_any_width` went red on a COMMENT that
    explains why `table-layout: fixed` is not used here. A guard that reads the
    prose around the CSS is testing the documentation, not the stylesheet.
    """
    return re.sub(r"/\*.*?\*/", "", _css_block(), flags=re.DOTALL)


def _css_selector_rules() -> list:
    r"""[(selector, body)] for every rule in Screen 16's block, comments gone.

    ⚠️ THE OBVIOUS PATTERN IS WRONG AND IT COST A GREEN TEST. Anchoring on
    `(?:^|[{}])` CONSUMES the brace, and `re.findall` does not overlap — so the
    closing brace of one rule is eaten by its own match and is no longer there to
    anchor the next one. The scan returned every OTHER rule, and the
    page-scope guard was quietly checking half the stylesheet.
    ⭐ Fixed with a LOOKBEHIND, which asserts the brace without consuming it. A
    rule nested in an `@media` block is found too: the media block's own `{`
    anchors it, while `@media` itself is skipped by the `[^{}@\s]` first char.
    """
    return [(sel.strip(), body) for sel, body in re.findall(
        r"(?:(?<=\})|(?<=\{)|\A)\s*([^{}@\s][^{}]*?)\s*\{([^{}]*)\}",
        _css_rules())]


def _seed_scoring_and_costs(cfg) -> None:
    """The fixture config dir has neither file, which is the UNAVAILABLE case.
    Writing them exercises the populated case in the same run, so neither path
    is asserted only in the abstract."""
    d = cfg["paths"]["config_dir"]
    with open(os.path.join(d, "scoring_weights.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump({"steps": {"volume_surge": 15, "vwap_position": 10,
                                  "atr_filter": 10, "rsi_range": 10,
                                  "price_action": 15, "sector_strength": 10,
                                  "time_of_day": 5, "spread_check": 5,
                                  "circuit_check": 10, "signal_age": 10},
                        "min_pass_score": 60, "high_score_threshold": 80,
                        "medium_score_threshold": 65}, fh, sort_keys=False)
    with open(os.path.join(d, "broker_costs.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump({"zerodha": {"brokerage_pct_intraday": 0.03,
                                    "brokerage_flat_intraday": 20.0,
                                    "stt_sell_pct": 0.025, "gst_pct": 18.0,
                                    "exchange_txn_pct": 0.00297,
                                    "sebi_pct": 0.0001,
                                    "stamp_duty_mis_buy_pct": 0.003}}, fh,
                       sort_keys=False)


# ─────────────────────────────────────────────────────────────────────────────
# The endpoint contract
# ─────────────────────────────────────────────────────────────────────────────
class TestScreenEndpoint:

    def test_payload_has_every_block_the_artwork_draws(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        for key in ("header", "kpis", "categories", "trading_hours", "capital",
                    "risk", "position_sizing", "scoring", "slippage",
                    "mode_specific", "global_limits", "moved_to_mode_panel",
                    "broker_costs", "comparison", "history",
                    "search_categories", "changed_by_note"):
            assert key in d, f"missing block: {key}"
        # ⛔ THE STRATEGY DATA PATH IS GONE, not merely hidden by the
        # template (the revised design: "Remove S16 Strategy Configuration UI
        # AND its data path"). A payload key nobody renders is a panel waiting
        # to be re-added by accident.
        assert "strategies" not in d

    def test_the_six_artwork_kpis_are_present_in_order(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert [k["key"] for k in d["kpis"]] == [
            "trade_mode", "broker", "max_open_positions", "max_daily_trades",
            "min_pass_score", "daily_loss_limit"]
        assert [k["label"] for k in d["kpis"]] == [
            "Trade Mode", "Broker", "Max Open Positions", "Max Daily Trades",
            "Minimum Pass Score", "Daily Loss Limit"]

    def test_the_ten_revised_categories_are_present_in_order(self, client) -> None:
        """The revised design names ten categories. ⛔ "Strategies" and
        "Scanners" are NOT among them — Screen 17 owns strategy control and
        Screen 21 owns the scanner mapping."""
        d = client.get("/api/config/screen").get_json()
        assert [c["label"] for c in d["categories"]] == [
            "System", "Trading Hours", "Capital", "Risk", "Position Sizing",
            "Scoring", "Slippage", "Alerts", "Broker", "Advanced"]
        assert {"strategies", "scanners"} & {c["key"] for c in d["categories"]} == set()

    def test_the_old_config_contract_is_untouched(self, client) -> None:
        """⛔ ADDITIVE ONLY. `/api/config` has its own contract test and other
        callers; Screen 16 rides a SECOND endpoint."""
        d = client.get("/api/config").get_json()
        assert [g["name"] for g in d["groups"]] == [
            "System", "Risk", "Capital", "Strategies", "Scanners", "Execution",
            "Smart Target", "Slippage"]
        assert "drift" in d and "header" in d

    def test_both_new_routes_require_auth(self, app) -> None:
        anon = app.test_client()
        for ep in ("/api/config/screen", "/api/export/config-screen"):
            assert anon.get(ep).status_code == 401, ep

    def test_the_screen_route_renders(self, client) -> None:
        r = client.get("/config")
        assert r.status_code == 200
        assert b"cfg16-page" in r.data


# ─────────────────────────────────────────────────────────────────────────────
# ⛔ READ-ONLY. Screen 16 is an information board.
# ─────────────────────────────────────────────────────────────────────────────
class TestNoOperationalControl:

    def test_screen16_routes_accept_get_only(self, app) -> None:
        seen = {}
        for rule in app.url_map.iter_rules():
            if str(rule) in ("/api/config/screen", "/api/export/config-screen"):
                seen[str(rule)] = set(rule.methods)
        assert set(seen) == {"/api/config/screen", "/api/export/config-screen"}
        for path, methods in seen.items():
            assert methods <= {"GET", "HEAD", "OPTIONS"}, (path, methods)

    def test_the_template_has_no_mutating_call(self) -> None:
        m = _markup()
        for banned in ('method: "POST"', "method: 'POST'", "method:'POST'",
                       '@submit', '<form'):
            assert banned not in m, banned
        # No control verbs from Screen 17's surface.
        for word in ("Apply Changes", "Cancel", "Revert", "toggleStrategy",
                     "applyLimits", "confirmText", "control plane"):
            assert word not in m, word

    def test_no_switch_pill_or_toggle_survives_anywhere(self) -> None:
        """The screen used to render a read-only state PILL where the artwork
        drew a switch. ⛔ With Strategy Configuration removed there is no state
        to pill at all, so the class itself must be gone — a leftover pill rule
        is how the panel comes back."""
        m = _markup()
        assert "cfg16-pill" not in m                  # the old strategy state pill
        assert "ctl-sw" not in m                      # Screen 17's switch class
        assert 'type="checkbox"' not in m
        # The ONLY buttons are view-state: tabs, popular-search chips, view-all,
        # and the rail's compact/full-values toggle. ⭐ EVERY ONE OF THEM CHANGES
        # WHAT IS DISPLAYED, ⛔ never what is configured — the allow-list is the
        # thing that keeps an action button from arriving unnoticed, so a new
        # class is added here deliberately or not at all.
        # ⚠️ `(?<=\s)` is load-bearing: Alpine's `:class="…"` binding also ends
        # in `class="`, so a looser pattern reads the ternary as a class list.
        classes = re.findall(r'<button[^>]*?(?<=\s)class="([^"]+)"', m)
        for cls in classes:
            assert any(k in cls for k in ("cfg16-tab", "cfg16-chip", "cfg16-more",
                                          "cfg16-expand")), cls

    def test_the_page_talks_to_exactly_the_two_read_only_endpoints(self) -> None:
        m = _markup()
        endpoints = set(re.findall(r'"(/api/[a-z0-9/\-]+)"', m))
        assert endpoints == {"/api/config/screen", "/api/export/config-screen"}


# ─────────────────────────────────────────────────────────────────────────────
# ⛔ NEITHER A STRATEGY NOR A SCANNER IS A CONFIGURATION OBJECT HERE.
# Screen 17 owns strategy control; Screen 21 owns the scanner mapping.
# ─────────────────────────────────────────────────────────────────────────────
class TestNoStrategyOrScannerSurface:

    def test_the_template_draws_no_strategy_or_scanner_panel(self) -> None:
        m = _markup()
        for gone in ("STRATEGY CONFIGURATION", "SCANNER MAPPING", "Strategy Name",
                     "Scanner URL", "Trade Type", "chartink"):
            assert gone.lower() not in m.lower(), gone
        # ⭐ NON-VACUITY: the identical search over the identical text DOES find
        # the two panels that replaced them, so a miss above is absence, ⛔ not
        # a search that stopped working.
        for present in ("MODE-SPECIFIC CONFIGURATION", "GLOBAL LIMITS",
                        "Intraday", "Delivery"):
            assert present in m, present

    def test_no_category_tab_carries_a_strategy_or_scanner_row(self, client) -> None:
        """⭐ The panel could be gone while the data walked back in through the
        category tabs. Every tab's rows are swept for a strategy name."""
        d = client.get("/api/config/screen").get_json()
        for cat in d["categories"]:
            for row in cat["rows"]:
                name = str(row["parameter"])
                assert not (name.endswith("_long") or name.endswith("_short")),                     (cat["key"], name)
                assert "scanner" not in name.lower() or name.startswith("system."),                     (cat["key"], name)

    def test_no_strategy_yaml_is_read_for_this_screen(self, client, gui_config) -> None:
        """⛔ THE DATA PATH, not just the panel. The fixture configures six
        strategies; if any of them reached the payload the screen would still be
        a strategy surface. ⭐ Non-vacuous: the same names ARE present in the
        old `/api/config` payload, which this screen must not have touched."""
        text = client.get("/api/config/screen").get_data(as_text=True)
        for name in ("gap_fade_long", "vwap_bounce_long", "range_breakout_long",
                     "first_pullback_long", "gap_fade_short"):
            assert name not in text, name
        old = client.get("/api/config").get_data(as_text=True)
        assert "vwap_bounce_long" in old, "control: /api/config still carries strategies"


# ─────────────────────────────────────────────────────────────────────────────
# ⛔ NOTHING IS HARD-CODED FROM THE ARTWORK.
# ─────────────────────────────────────────────────────────────────────────────
class TestValuesComeFromConfiguration:

    def test_risk_values_are_the_fixtures_own_not_the_artworks(self, client) -> None:
        """The mode-split limits now live in the Mode-Specific panel, and each
        one is still the FIXTURE's value, ⛔ never the screenshot's."""
        d = client.get("/api/config/screen").get_json()
        mode = {m["label"]: m for m in d["mode_specific"]}
        assert mode["Max Positions (Open)"]["intraday"] == 5    # artwork draws 10
        assert mode["Max Positions (Open)"]["delivery"] == 3
        assert mode["Max Trades (Per Day)"]["intraday"] == 10   # artwork draws 50
        assert mode["Max Trades (Per Day)"]["delivery"] == 5
        assert mode["Daily Loss Limit"]["intraday"] == 0.03     # artwork draws 5.00%
        assert mode["Sector Exposure"]["intraday"] == 0.40      # artwork draws 25.00%
        glob = {g["label"]: g["value"] for g in d["global_limits"]}
        assert glob["Max Consecutive Losses"] == 5              # artwork draws 3

    def test_the_artworks_mock_numbers_do_not_appear_anywhere(self, client) -> None:
        """⭐ The reverse of the test above, and the one that would catch a value
        copied off the screenshot."""
        d = client.get("/api/config/screen").get_json()
        kpis = {k["key"]: k["value"] for k in d["kpis"]}
        assert kpis["max_open_positions"] != 10
        assert kpis["max_daily_trades"] != 50
        assert kpis["daily_loss_limit"] != "5.00%"
        # ⭐ NON-VACUITY: the same cards DO carry the fixture's own numbers, so
        # the inequalities above are about the values, ⛔ not about the keys
        # having quietly gone missing.
        assert kpis["max_open_positions"].startswith("5 /")
        assert kpis["max_daily_trades"].startswith("10 /")
        assert kpis["daily_loss_limit"].startswith("3.00% /")

    def test_a_percentage_carries_its_base(self, client) -> None:
        """⛔ A bare percentage is not a number. The daily-loss KPI names the
        base its limit is a percentage OF."""
        d = client.get("/api/config/screen").get_json()
        loss = next(k for k in d["kpis"] if k["key"] == "daily_loss_limit")
        assert "opening capital" in (loss["sub"] or "")
        # Every percentage row in the mode panel names what it is a percentage
        # OF — ⛔ a bare "40.00%" beside "40.00%" says nothing.
        for m in d["mode_specific"]:
            if m["unit"] == "pct":
                assert m["note"], m["label"]

    def test_capital_allocations_are_percentages_not_rupees(self, client) -> None:
        """⛔ NO rupee capital value exists in configuration — the artwork's
        '₹5,00,000' has no source and must not be manufactured."""
        d = client.get("/api/config/screen").get_json()
        alloc = next(m for m in d["mode_specific"] if m["label"] == "Capital Allocation")
        assert alloc["intraday"] == 0.70 and alloc["delivery"] == 0.30
        for block in ("capital", "risk", "position_sizing"):
            for r in d[block]:
                assert "₹" not in str(r["value"]), (block, r["label"])

    def test_time_format_matches_the_artwork(self) -> None:
        assert config_view._time12("09:15") == "09:15 AM"
        assert config_view._time12("15:30") == "03:30 PM"
        assert config_view._time12("00:05") == "12:05 AM"
        assert config_view._time12("12:00") == "12:00 PM"
        assert config_view._time12(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# An unavailable value is shown as unavailable, never substituted.
# ─────────────────────────────────────────────────────────────────────────────
class TestUnavailableIsHonest:

    def test_scoring_is_unavailable_when_the_file_is_absent(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert d["scoring"]["available"] is False
        assert d["scoring"]["weights"] == []
        assert d["scoring"]["total_weight"] is None
        assert d["scoring"]["min_pass_score"] is None     # ⛔ not 70, not 0

    def test_broker_costs_are_unavailable_when_the_file_is_absent(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert [r["label"] for r in d["broker_costs"]] == [
            "Brokerage", "STT", "GST", "Exchange Charges", "SEBI Charges",
            "Stamp Duty"]
        assert all(r["value"] is None for r in d["broker_costs"])

    def test_trading_hours_are_unavailable_when_the_snapshot_has_none(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert [r["label"] for r in d["trading_hours"]] == [
            "Market Open", "Market Close", "Entry Start", "Entry End",
            "EOD Entry Cutoff", "Squareoff Time"]
        assert all(r["value"] is None for r in d["trading_hours"])

    def test_the_same_blocks_populate_once_their_sources_exist(self, gui_config, app) -> None:
        """⭐ THE CONTROL for the three tests above: identical assertions, files
        present. Without this, 'None' would prove nothing — a reader that always
        returned None would pass every unavailable test."""
        _seed_scoring_and_costs(gui_config)
        d = config_view.build_config_center(gui_config)
        assert d["scoring"]["available"] is True
        assert d["scoring"]["min_pass_score"] == 60
        assert d["scoring"]["total_weight"] == 100
        assert len(d["scoring"]["weights"]) == 10
        costs = {r["label"]: r["value"] for r in d["broker_costs"]}
        assert costs["GST"] == "18.00%"
        assert costs["Brokerage"] == "0.0300%"
        assert costs["Exchange Charges"] == "0.00297%"

    def test_scoring_factor_labels_follow_the_artwork(self, gui_config) -> None:
        _seed_scoring_and_costs(gui_config)
        d = config_view.build_config_center(gui_config)
        assert [w["factor"] for w in d["scoring"]["weights"]] == [
            "Volume Surge", "VWAP", "ATR", "RSI", "Price Action",
            "Sector Strength", "Time Of Day", "Spread", "Circuit", "Signal Age"]

    def test_an_unknown_scoring_factor_is_shown_not_dropped(self, gui_config) -> None:
        """⛔ A weight that vanished from the panel but still counted toward the
        total would make the total unexplainable."""
        path = os.path.join(gui_config["paths"]["config_dir"], "scoring_weights.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"steps": {"volume_surge": 60, "brand_new_factor": 40},
                            "min_pass_score": 50}, fh, sort_keys=False)
        d = config_view.build_config_center(gui_config)
        assert [w["factor"] for w in d["scoring"]["weights"]] == [
            "Volume Surge", "Brand New Factor"]
        assert d["scoring"]["total_weight"] == 100


# ─────────────────────────────────────────────────────────────────────────────
# Slippage: derived from the gate that enforces it.
# ─────────────────────────────────────────────────────────────────────────────
class TestSlippage:

    def test_bands_are_built_from_the_configured_tiers(self) -> None:
        out = config_view._slippage({"entry_gate": {"slippage_control": {
            "mode": "sl_fraction", "max_slippage_fraction": 0.22,
            "tiers": [{"max_price": 100, "max_slippage_rs": 1.0},
                      {"max_price": 200, "max_slippage_rs": 1.25},
                      {"max_price": 500, "max_slippage_rs": 2.0},
                      {"max_price": 999999, "max_slippage_rs": 3.0}]}}})
        assert [r["band"] for r in out["rows"]] == [
            "0 - 100", "100 - 200", "200 - 500", "500+"]
        assert [r["max_rs"] for r in out["rows"]] == [1.0, 1.25, 2.0, 3.0]
        # the percentage control is a fraction of the STOP, and is stated as such
        assert all(r["pct_of_sl"] == "22.00%" for r in out["rows"])

    def test_no_band_is_invented_when_no_tier_is_configured(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert d["slippage"]["rows"] == []          # fixture configures no tiers
        assert d["slippage"]["mode"] == "sl_fraction"


# ─────────────────────────────────────────────────────────────────────────────
# Comparison + history come from real snapshot diffs.
# ─────────────────────────────────────────────────────────────────────────────
class TestComparisonAndHistory:

    def test_comparison_is_a_real_parameter_level_diff(self, client) -> None:
        """The fixture's two snapshots differ in exactly one leaf:
        risk.max_daily_trades 9 → 10."""
        d = client.get("/api/config/screen").get_json()
        rows = d["comparison"]["rows"]
        assert [r["parameter"] for r in rows] == ["risk.max_daily_trades"]
        assert rows[0]["old"] == "9" and rows[0]["new"] == "10"
        assert rows[0]["module"] == "Risk"
        assert d["comparison"]["previous_date"] and d["comparison"]["current_date"]

    def test_history_expands_each_hash_transition_to_parameters(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert d["history"], "the fixture has one real transition"
        assert {h["parameter"] for h in d["history"]} == {"risk.max_daily_trades"}
        assert all(h["module"] == "Risk" for h in d["history"])

    def test_changed_by_is_never_fabricated(self, client) -> None:
        """⛔ The system records no per-change author. The column is unavailable,
        and the reason is stated in the SERVED HTML — ⛔ not only in a payload a
        reader would have to run JavaScript to see."""
        d = client.get("/api/config/screen").get_json()
        assert all(h["changed_by"] is None for h in d["history"])
        assert "not captured" in d["changed_by_note"]
        html = client.get("/config").get_data(as_text=True).lower()
        assert "not captured" in html
        # ⭐ The template sentence and the payload note are pinned to the same
        # phrase so they cannot drift into saying two different things.
        assert "not captured" in d["changed_by_note"].lower()

    def test_a_hash_change_alone_never_produces_a_row(self) -> None:
        """⭐ A hash says THAT something changed, never WHAT. Identical bodies
        must diff to nothing even if the caller believed they differed."""
        assert config_view._leaf_diff({"risk": {"a": 1}}, {"risk": {"a": 1}}) == []
        assert config_view._leaf_diff({"risk": {"a": 1}}, {"risk": {"a": 2}}) == [
            {"parameter": "risk.a", "module": "Risk", "old": "1", "new": "2"}]


# ─────────────────────────────────────────────────────────────────────────────
# Categories: nothing is silently dropped.
# ─────────────────────────────────────────────────────────────────────────────
class TestCategoriesCoverEverything:

    def test_every_snapshot_leaf_is_reachable_from_some_tab(self, client) -> None:
        """⛔ 'Advanced' is the remainder bucket, so no configuration key can be
        invisible on this screen. A new top-level key added upstream lands there
        instead of disappearing."""
        d = client.get("/api/config/screen").get_json()
        shown = set()
        for c in d["categories"]:
            for r in c["rows"]:
                shown.add(r["parameter"])
        raw = client.get("/api/config").get_json()
        leaves = set()
        for g in raw["groups"]:
            if g["name"] in ("Strategies", "Scanners"):
                continue                     # file-sourced, not snapshot leaves
            for k, v in (g["data"] or {}).items():
                leaves |= set(config_view._flatten({k: v}))
        # ⚠️ `/api/config` HOISTS slippage_control out of entry_gate (its own
        # grouping), while Screen 16 shows the real dotted path. Same leaf, two
        # names — normalise instead of excusing it, so the coverage claim stays
        # a real one.
        def norm(k):
            return k[len("entry_gate."):] if k.startswith("entry_gate.slippage_control") else k
        shown_n = {norm(k) for k in shown}
        missing = {leaf for leaf in leaves if norm(leaf) not in shown_n}
        assert not missing, sorted(missing)[:10]

    def test_advanced_is_computed_not_listed(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        adv = next(c for c in d["categories"] if c["key"] == "advanced")
        assert adv["count"] > 0
        claimed = set()
        for c in d["categories"]:
            if c["key"] in ("advanced", "scoring", "strategies", "scanners",
                            "broker", "slippage"):
                continue
            claimed |= {r["parameter"].split(".")[0] for r in c["rows"]}
        assert not ({r["parameter"].split(".")[0] for r in adv["rows"]} & claimed)


# ─────────────────────────────────────────────────────────────────────────────
# Export: a real workbook, filtered, from the same payload.
# ─────────────────────────────────────────────────────────────────────────────
class TestExport:

    def test_export_returns_a_real_xlsx_with_the_artworks_sheets(self, client) -> None:
        r = client.get("/api/export/config-screen")
        assert r.status_code == 200
        assert r.mimetype == ("application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")
        assert r.data[:2] == b"PK"                   # a real zip container
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(r.data))
        assert wb.sheetnames == ["KPIs", "Trading Hours", "Capital", "Risk",
                                 "Position Sizing", "Scoring", "Slippage",
                                 "Mode-Specific", "Global Limits",
                                 "Broker Costs", "Comparison", "History"]
        rows = list(wb["Risk"].values)
        assert rows[0] == ("Parameter", "Value", "Note")
        assert any(r and r[0] == "Sector Cap Mode" for r in rows)

    def test_no_strategies_sheet_survives(self, client) -> None:
        """⛔ A workbook that still carried the sheet would let a reader
        reconstruct exactly the strategy table the screen no longer owns."""
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        assert "Strategies" not in wb.sheetnames

    def test_the_mode_sheet_carries_both_columns_with_their_units(self, client) -> None:
        """⛔ A bare 0.1 in a spreadsheet cell is unreadable and 10% written as
        10 is wrong — the unit travels with the number, exactly as on screen."""
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        rows = list(wb["Mode-Specific"].values)
        assert rows[0] == ("Parameter", "Intraday", "Delivery",
                           "Config Key", "Note")
        by = {r[0]: r for r in rows[1:]}
        assert by["Max Positions (Open)"][1] == 5
        assert by["Max Positions (Open)"][2] == 3
        assert by["Capital Allocation"][1] == "70.00%"
        assert by["Capital Allocation"][2] == "30.00%"
        # every row names BOTH configuration keys it read
        for r in rows[1:]:
            assert " / " in str(r[3]), r

    def test_a_category_search_selects_the_category_not_empties_it(self, client) -> None:
        """⚠️ FOUND BY USING THE SCREEN: the approved popular-search chips are
        CATEGORY names, and no cell in the Slippage table contains the word
        "slippage" — a cell-only match emptied the panel the chip names. The
        sheet/panel name is part of the match on BOTH sides."""
        from openpyxl import load_workbook
        full = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        hit = load_workbook(io.BytesIO(
            client.get("/api/export/config-screen?q=slippage").data))
        assert len(list(hit["Slippage"].values)) == len(list(full["Slippage"].values))
        # ⭐ and it still FILTERS: an unrelated sheet loses its rows.
        assert len(list(hit["Risk"].values)) < len(list(full["Risk"].values))

    def test_export_honours_the_search_filter(self, client) -> None:
        from openpyxl import load_workbook
        full = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        filt = load_workbook(io.BytesIO(
            client.get("/api/export/config-screen?q=unknown").data))
        assert full.sheetnames == filt.sheetnames        # sheets are never dropped
        full_rows = len(list(full["Risk"].values))
        filt_rows = len(list(filt["Risk"].values))
        assert 1 < filt_rows < full_rows                 # header + the match only
        assert any(r and "Unknown" in str(r[0]) for r in filt["Risk"].values)

    def test_export_cells_come_from_the_same_payload_as_the_screen(self, client) -> None:
        from openpyxl import load_workbook
        d = client.get("/api/config/screen").get_json()
        wb = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        screen = {r["label"]: r["value"] for r in d["risk"]}
        for row in list(wb["Risk"].values)[1:]:
            # None in the payload is written as the unavailable marker, never as
            # a blank cell and never as a substituted number.
            expected = screen[row[0]]
            expected = config_view._UNAVAILABLE if expected is None else expected
            assert str(expected) == str(row[1]), row


# ─────────────────────────────────────────────────────────────────────────────
# MODE SEPARATION — the point of the 02-Sep revision.
#
# 🔬 THE SPLIT SET WAS MEASURED FROM THE ENFORCERS AT THE DEPLOYED SHA
#    (`origin/main` 39292d3), ⛔ not from key names:
#      · position_sizer.py:375-384  — the three sizing limits
#      · risk_engine.py:326-343     — the two percentage gates + the two counts
#      · risk_engine.py:557-560 / :655-660 — OPEN_POSITIONS and DAILY_TRADES
#        BRANCH on bucket == "positional": a delivery entry never consults the
#        intraday cap at all.
#    ⛔ `max_consecutive_losses` is deliberately shared (risk_engine.py:642-644).
#
# `_DEPLOYED_SYSTEM` below mirrors that configuration exactly, so these tests
# fail the day a delivery key is added, renamed or dropped upstream.
# ─────────────────────────────────────────────────────────────────────────────
_DEPLOYED_SYSTEM = {
    "capital": {
        "intraday_bucket_pct": 0.70, "positional_bucket_pct": 0.30,
        "leverage_map": {"INTRADAY": 5.0, "COVER_ORDER": 6.0,
                         "DELIVERY": 1.0, "BRACKET_ORDER": 5.0},
    },
    "position_sizing": {
        "risk_per_trade_pct": 0.01, "max_concentration_pct": 0.10,
        "max_position_value_pct": 0.40, "max_single_order_qty": 10000,
        "delivery_risk_per_trade_pct": 0.01,
        "delivery_max_concentration_pct": 0.10,
        "delivery_max_position_value_pct": 0.40,
    },
    "risk": {
        "max_open_positions": 5, "max_daily_trades": 10,
        "max_open_delivery_positions": 3, "max_daily_delivery_trades": 5,
        "max_sector_exposure_pct": 0.40, "delivery_max_sector_exposure_pct": 0.40,
        "daily_loss_limit_pct": 0.03, "delivery_daily_loss_limit_pct": 0.03,
        "max_consecutive_losses": 4, "price_drift_threshold": 0.005,
        "sector_cap_mode": "observe",
    },
}


class TestModeSeparation:

    def test_every_split_parameter_reads_its_own_two_keys(self) -> None:
        """Each row's two values come from two INDEPENDENT keys, and the row
        names both."""
        rows = {m["label"]: m for m in config_view._mode_specific(_DEPLOYED_SYSTEM)}
        assert set(rows) == {
            "Capital Allocation", "Leverage", "Risk Per Trade",
            "Max Concentration", "Max Position Value", "Max Positions (Open)",
            "Max Trades (Per Day)", "Daily Loss Limit", "Sector Exposure"}
        assert rows["Max Positions (Open)"]["intraday"] == 5
        assert rows["Max Positions (Open)"]["delivery"] == 3
        assert rows["Max Trades (Per Day)"]["intraday"] == 10
        assert rows["Max Trades (Per Day)"]["delivery"] == 5
        assert rows["Leverage"]["intraday"] == 5.0
        assert rows["Leverage"]["delivery"] == 1.0
        for row in rows.values():
            assert " / " in row["source"], row["label"]

    def test_the_delivery_column_never_inherits_the_intraday_value(self) -> None:
        """⭐ THE CENTRAL GUARANTEE, and the one an inheritance bug would break
        silently: drop each delivery key in turn and the delivery cell must go
        UNAVAILABLE while the intraday cell is untouched. ⛔ It must never fall
        back to the intraday number — the running system REJECTS that config at
        boot rather than borrowing."""
        import copy
        base = {m["label"]: m for m in config_view._mode_specific(_DEPLOYED_SYSTEM)}
        for group, key, label in (
                ("position_sizing", "delivery_risk_per_trade_pct", "Risk Per Trade"),
                ("position_sizing", "delivery_max_concentration_pct", "Max Concentration"),
                ("position_sizing", "delivery_max_position_value_pct", "Max Position Value"),
                ("risk", "max_open_delivery_positions", "Max Positions (Open)"),
                ("risk", "max_daily_delivery_trades", "Max Trades (Per Day)"),
                ("risk", "delivery_daily_loss_limit_pct", "Daily Loss Limit"),
                ("risk", "delivery_max_sector_exposure_pct", "Sector Exposure"),
                ("capital", "positional_bucket_pct", "Capital Allocation")):
            sysd = copy.deepcopy(_DEPLOYED_SYSTEM)
            del sysd[group][key]
            row = next(m for m in config_view._mode_specific(sysd)
                       if m["label"] == label)
            assert row["delivery"] is None, (key, row["delivery"])
            assert row["intraday"] == base[label]["intraday"], key
            assert row["partial"] is True, key

    def test_a_global_parameter_is_shown_once_and_says_why(self) -> None:
        """⛔ THE CONVERSE RULE. A parameter with no delivery twin must not be
        printed under two headings — that is a fabricated distinction. It is
        shown once, WITH the reason it governs both books."""
        scoring = {"min_pass_score": 60}
        rows = {g["label"]: g for g in
                config_view._global_limits(_DEPLOYED_SYSTEM, scoring)}
        assert set(rows) == {"Minimum Eligible Score", "Max Qty (Per Order)",
                             "Max Consecutive Losses", "Price Drift Threshold"}
        for row in rows.values():
            assert row["reason"], row["label"]
            assert "intraday" not in row and "delivery" not in row
        # ⛔ and no global label is duplicated into the mode panel
        split = {m["label"] for m in config_view._mode_specific(_DEPLOYED_SYSTEM)}
        assert split & set(rows) == set()

    def test_the_four_named_parameters_are_sourced_from_the_right_keys(self) -> None:
        """The revised design names four parameters to verify by hand. Each is
        asserted against the key that actually reaches an enforcer, and each is
        then MOVED, so the assertion cannot be reading a constant."""
        import copy
        scoring = {"min_pass_score": 60}

        # Minimum Eligible Score — scoring_weights.yaml, NOT the v3_chain seed
        glob = {g["label"]: g for g in
                config_view._global_limits(_DEPLOYED_SYSTEM, scoring)}
        assert glob["Minimum Eligible Score"]["value"] == 60
        assert glob["Minimum Eligible Score"]["source"] == \
            "scoring_weights.yaml min_pass_score"
        moved = config_view._global_limits(_DEPLOYED_SYSTEM, {"min_pass_score": 55})
        assert next(g for g in moved
                    if g["label"] == "Minimum Eligible Score")["value"] == 55

        # Max Qty — position_sizing.max_single_order_qty (SHARES, and global)
        assert glob["Max Qty (Per Order)"]["value"] == 10000
        assert glob["Max Qty (Per Order)"]["source"] == \
            "position_sizing.max_single_order_qty"
        sysd = copy.deepcopy(_DEPLOYED_SYSTEM)
        sysd["position_sizing"]["max_single_order_qty"] = 7777
        assert next(g for g in config_view._global_limits(sysd, scoring)
                    if g["label"] == "Max Qty (Per Order)")["value"] == 7777

        # Max Position Value + Max Concentration — split, and each column moves
        # only with ITS OWN key
        sysd = copy.deepcopy(_DEPLOYED_SYSTEM)
        sysd["position_sizing"]["delivery_max_position_value_pct"] = 0.25
        sysd["position_sizing"]["max_concentration_pct"] = 0.08
        rows = {m["label"]: m for m in config_view._mode_specific(sysd)}
        assert rows["Max Position Value"]["intraday"] == 0.40
        assert rows["Max Position Value"]["delivery"] == 0.25
        assert rows["Max Concentration"]["intraday"] == 0.08
        assert rows["Max Concentration"]["delivery"] == 0.10

    def test_equal_today_is_not_collapsed_into_one_column(self) -> None:
        """⚠️ Six of the nine rows hold EQUAL numbers at the deployed config.
        ⛔ Equal is not shared: the keys are separately settable, and collapsing
        the row would make the screen wrong the first time one moves."""
        rows = config_view._mode_specific(_DEPLOYED_SYSTEM)
        same = [m for m in rows if m["same"]]
        assert len(same) >= 4, "the fixture must exercise the equal case"
        for m in same:
            assert m["intraday"] is not None and m["delivery"] is not None
            assert "delivery" in m["source"].lower()

    def test_a_split_kpi_shows_both_books_never_one(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        split = {k["key"] for k in d["kpis"] if k.get("split")}
        assert split == {"max_open_positions", "max_daily_trades",
                         "daily_loss_limit"}
        for k in d["kpis"]:
            if k.get("split"):
                assert " / " in str(k["value"]), k["key"]
                assert "Intraday / Delivery" in (k["sub"] or ""), k["key"]

    def test_the_quad_no_longer_prints_a_mode_split_value(self, client) -> None:
        """⛔ NOTHING IS SHOWN TWICE, and nothing mode-split is shown as one
        number. The quad's own labels are checked against the mode panel's."""
        d = client.get("/api/config/screen").get_json()
        split = {m["label"] for m in d["mode_specific"]}
        quad = {r["label"] for block in ("capital", "risk", "position_sizing")
                for r in d[block]}
        assert quad & split == set(), quad & split
        for gone in ("Max Open Positions", "Max Daily Trades", "Daily Loss Limit %",
                     "Sector Exposure %", "Risk Per Trade", "Max Position Size",
                     "Max Concentration", "Intraday Allocation",
                     "Delivery Allocation", "Leverage"):
            assert gone not in quad, gone
        # ⭐ NON-VACUITY: the quad is not simply empty.
        assert len(quad) >= 12, quad

    def test_no_delivery_scoped_key_in_the_config_is_left_off_the_panel(self, client) -> None:
        """⭐ COMPLETENESS, from the CONFIG rather than from a list I wrote.

        Every leaf the running configuration carries is reachable from some
        category tab, so the tabs are swept for keys that are delivery-scoped by
        NAME, and each one must be named in a Mode-Specific row's source. ⛔ The
        failure this catches is the one a hand-written list cannot: a NEW
        delivery key added upstream that this screen silently does not show.
        🔬 Measured at the deployed SHA: the configuration holds exactly SEVEN
        such keys and the panel names all seven.
        """
        def delivery_keys(names):
            return {n for n in names
                    if n.startswith("delivery_") or "_delivery_" in n}

        d = client.get("/api/config/screen").get_json()
        # ⚠️ THE SHARED FIXTURE CARRIES ONLY TWO OF THE SEVEN, and a mutation
        # proved that made this test miss five of them. The sweep therefore runs
        # over BOTH the served payload AND `_DEPLOYED_SYSTEM`, which mirrors the
        # configuration production actually loads.
        live = delivery_keys(str(row["parameter"]).split(".")[-1]
                             for cat in d["categories"] for row in cat["rows"])
        deployed = delivery_keys(p.split(".")[-1] for p in
                                 config_view._flatten(_DEPLOYED_SYSTEM))
        assert len(deployed) == 7, sorted(deployed)   # the measured count
        assert live, "the fixture must carry at least one delivery-scoped key"

        sources = " ".join(m["source"] for m in
                           config_view._mode_specific(_DEPLOYED_SYSTEM))
        served = " ".join(m["source"] for m in d["mode_specific"])
        for key in sorted(deployed):
            assert key in sources, key
        for key in sorted(live):
            assert key in served, key

    def test_each_quad_card_declares_how_many_of_its_own_moved(self, client) -> None:
        """A card shorter than the artwork's must say WHY, never read as a panel
        that quietly lost rows. The counts sum to the mode panel's own length,
        so the note and the table cannot drift apart."""
        d = client.get("/api/config/screen").get_json()
        moved = d["moved_to_mode_panel"]
        assert set(moved) == {"capital", "risk", "position_sizing"}
        assert sum(moved.values()) == len(d["mode_specific"])
        assert all(v > 0 for v in moved.values()), moved

    def test_the_template_renders_both_columns_and_the_shared_reason(self) -> None:
        """The payload can be right while the template prints one column. The
        markup is checked for both mode headers, both cells, and the GLOBAL
        panel's reason column."""
        m = _markup()
        assert 'x-text="modeVal(m.intraday, m.unit)"' in m
        assert 'x-text="modeVal(m.delivery, m.unit)"' in m
        assert 'x-text="g.reason"' in m
        assert ">Intraday<" in m and ">Delivery<" in m


# ─────────────────────────────────────────────────────────────────────────────
# THE RAIL IS SECONDARY, AND TRUNCATION IS NOT DATA LOSS.
#
# 📜 Rama, 02-Sep-2026: "Configuration Comparison and Configuration History are
# quite narrow. Long values … wrap into multiple lines and make the panels
# visually dense. Prefer sensible truncation/ellipsis with a clear
# tooltip/detail affordance … Comparison / History are secondary."
#
# ⛔ THE HAZARD THIS CLASS EXISTS FOR: a truncated cell whose full value is
# reachable ONLY by hovering is the weaker guarantee this project has been
# caught by before. There are THREE routes to every value — the `title`, the
# panel's FULL VALUES toggle, and the XLSX export — and the tests below hold
# all three open.
# ─────────────────────────────────────────────────────────────────────────────
class TestRailIsSecondaryButNothingIsLost:

    def test_every_rail_cell_carries_its_own_full_value_in_the_tooltip(self) -> None:
        """A cell may be shortened on screen; its `title` must be bound to the
        UNSHORTENED expression. ⛔ A title bound to the truncated text would
        make the tooltip useless and the value unreachable."""
        m = _markup()
        for expr in ("r.parameter", "r.old", "r.new",
                     "h.date", "h.module", "h.parameter", "h.old", "h.new"):
            assert ':title="%s"' % expr in m, expr
            # ⛔ and never the truncated form in the tooltip
            assert ':title="midTrunc(%s' % expr not in m, expr

    def test_the_shortened_path_keeps_its_TAIL_not_just_its_head(self) -> None:
        """🔬 FOUND BY LOOKING AT THE RENDER: plain end-ellipsis turned four
        consecutive comparison rows into the identical string
        "trading_hours.mis_squar…" — the panel showed four changes and named
        none of them. A configuration path is distinguished by its LAST
        segment, so the middle gives way and both ends survive."""
        js = _js_syntax.script_of(_tpl())
        i = js.index("midTrunc(s, n)")
        body = js[i:js.index("},", i)]
        assert "s.slice(0, head)" in body           # the head survives
        assert "s.slice(s.length - tail)" in body   # ⭐ and so does the tail
        assert "if (s.length <= n) return s;" in body   # short values untouched

    def test_the_path_cells_shorten_but_the_value_cells_do_not(self) -> None:
        """Only the dotted PATH is shortened in code — the value cells are left
        to CSS end-ellipsis, because a value's head is what identifies it."""
        m = _markup()
        assert 'x-text="railFull ? r.parameter : midTrunc(r.parameter, 24)"' in m
        assert 'x-text="railFull ? h.parameter : midTrunc(h.parameter, 11)"' in m
        for value_expr in ("r.old", "r.new", "h.old", "h.new"):
            assert 'midTrunc(%s' % value_expr not in m, value_expr

    def test_the_full_values_toggle_exists_on_both_rail_panels(self) -> None:
        """⭐ THE AFFORDANCE THAT MAKES TRUNCATION A PRESENTATION CHOICE. Without
        it the only route to a shortened value is a hover, which is no route at
        all for a keyboard or a screenshot."""
        m = _markup()
        assert m.count('class="cfg16-expand"') == 2
        assert m.count('@click="railFull = !railFull"') == 2
        assert ':aria-pressed="railFull"' in m
        # ⛔ view state only: it selects nothing and reaches nothing
        assert "railFull: false," in m

    def test_compact_mode_is_the_default_and_is_a_css_concern(self) -> None:
        """The one-line rule lives in CSS keyed off a class, so the same rows
        are in the DOM either way — ⛔ the toggle never removes a row."""
        m, block = _markup(), _css_rules()
        assert "cfg16-trunc" in m
        assert "text-overflow: ellipsis" in block
        assert "white-space: nowrap" in block
        # ⛔ THE TRUNCATION IS SCOPED TO THE OPT-IN CLASS, never to the page.
        # ⚠️ Checked per RULE, ⛔ not per line: the declaration sits on the second
        # line of its rule, and a line-wise check failed on its own formatting.
        owners = [sel for sel, body in _css_selector_rules()
                  if "text-overflow: ellipsis" in body]
        assert owners, "no rule declares the ellipsis"
        for sel in owners:
            assert ".cfg16-trunc" in sel, sel

    def test_the_export_is_never_truncated(self, client) -> None:
        """⭐ THE THIRD ROUTE, and the one that survives a screenshot: the
        workbook is built from the same payload and carries the whole value."""
        from openpyxl import load_workbook
        d = client.get("/api/config/screen").get_json()
        wb = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        sheet = {r[0]: r for r in list(wb["History"].values)[1:]}
        for h in d["history"]:
            row = sheet.get(h["date"])
            if row is None:
                continue
            assert "…" not in str(row), row     # no ellipsis reached the file
        assert all("…" not in str(c) for r in wb["Comparison"].values for c in r)


class TestGlobalLimitsColumnBalance:

    def test_the_reason_column_does_not_dwarf_the_value_it_explains(self) -> None:
        """📜 Rama, 02-Sep: keep the "why it is shared" column, "but ensure it
        does not become visually oversized compared with the actual value
        column." 🔬 It rendered 174 / 76 / 294 — the prose ran 3.9x the value.
        The declared shares are asserted here so a later edit cannot drift back."""
        block = _css_rules()
        got = {}
        for n in (1, 2):
            m = re.search(r"\.cfg16-globtbl \.dt-th:nth-child\(%d\) \{ width: (\d+)%%" % n,
                          block)
            assert m, "no declared width for global column %d" % n
            got[n] = int(m.group(1))
        reason = 100 - got[1] - got[2]
        assert got[2] >= 20, got            # the value column has real presence
        assert reason / got[2] <= 2.5, (reason, got[2])
        # ⭐ NON-VACUITY: the reason column still exists and is the widest.
        assert reason > got[2]

    def test_the_configured_number_reads_before_the_prose_beside_it(self) -> None:
        """Weight, ⛔ not size — the 13px floor holds, so the value is made
        primary by weight and colour rather than by shrinking the footnote."""
        block = _css_rules()
        assert re.search(r"\.cfg16-modetbl \.dt-num, \.cfg16-page \.cfg16-globtbl \.dt-num \{[^}]*font-weight: 600",
                         block), "the value cells must outweigh the scope prose"
        assert "cfg16-scope { color: var(--dim)" in block


# ─────────────────────────────────────────────────────────────────────────────
# THE CENTRED DATA-COLUMN HEADINGS.
#
# 📜 Rama, 02-Sep-2026: "The highlighted table headers … must be CENTER aligned
# … Do NOT center the entire tables. Only the specified/highlighted HEADER cells
# must be centered."
#
# 🔬 WHAT WAS ACTUALLY WRONG, measured before it was changed: the DATA under
# these columns was already centred by Rama's own 14-Aug global rule
# (`table td.dt-num { text-align: center !important }`); only the HEADERS were
# still right-aligned, so each heading sat off the edge of its own column. This
# is Screen 16 catching up with the 14-Aug column-role spec that Screen 08
# already follows — "data column HEADINGS …. CENTER (over their own data)".
# ⛔ NO DATA CELL ALIGNMENT IS CHANGED, and the global rule is not touched.
# ─────────────────────────────────────────────────────────────────────────────
class TestHighlightedHeadersAreCentred:

    #: The cells Rama highlighted, by the table that owns them. "Weight (%)" is
    #: ONE line of markup rendered TWICE (the artwork's two scoring columns), so
    #: five markup sites produce seven rendered header cells.
    HIGHLIGHTED = ("Weight (%)", "Allowed Slippage (₹)", "Allowed (% of stop)",
                   "Intraday", "Delivery")

    def test_exactly_the_highlighted_headers_carry_the_centring_class(self) -> None:
        m = _markup()
        for label in self.HIGHLIGHTED:
            assert 'cfg16-hc">%s</th>' % label in m, label
        # ⭐ THE GLOBAL LIMITS "Value", and ⛔ NOT the category tab's own "Value"
        # header, which Rama did not highlight and which must not move.
        assert 'cfg16-hc">Value</th>' in m
        assert m.count('cfg16-hc">Value</th>') == 1
        assert '<th class="dt-th">Value</th>' in m          # the category tab's
        # six markup sites, ⛔ no more
        assert len(re.findall(r'<th class="dt-th dt-num cfg16-hc">', m)) == 6

    def test_no_unhighlighted_header_is_centred(self) -> None:
        """⛔ THE HALF THAT MATTERS: "Do NOT center the entire tables." Every
        other data header on the page keeps the alignment it had."""
        m = _markup()
        for untouched in ("Parameter", "Factor", "Price Band (₹)", "Scope",
                          "Why it is shared", "Old Value", "New Value",
                          "Date", "Module", "Old", "New", "Changed By"):
            assert 'cfg16-hc">%s</th>' % untouched not in m, untouched

    def test_the_rule_targets_the_header_only(self) -> None:
        """⛔ `th`, never `td`. A rule that reached the body would re-align data
        Rama explicitly told me to leave alone."""
        block = _css_rules()
        owners = [(sel, body) for sel, body in _css_selector_rules()
                  if "cfg16-hc" in sel]
        assert [sel for sel, _ in owners] == [".cfg16-page .dt-th.cfg16-hc"], owners
        assert owners[0][1].strip() == "text-align: center;", owners[0]

    def test_the_shared_numeric_data_rule_is_untouched(self) -> None:
        """⭐ NON-VACUITY AND SCOPE IN ONE: the 14-Aug global rule that centres
        numeric BODY cells must still be exactly as it was — this correction
        rides on it, ⛔ it does not modify it."""
        with open(_CSS, encoding="utf-8") as fh:
            css = fh.read()
        assert "table td.dt-num,\n" in css
        assert "table td.ctr { text-align: center !important; }" in css
        assert ".dt-num { text-align: right; }" in css      # the header default

    def test_the_centred_headers_are_the_data_columns_of_their_tables(self) -> None:
        """Each centred header sits over a column the payload fills with
        NUMBERS — ⛔ not over a label column. Checked against the payload rather
        than against the markup's own class list."""
        m = _markup()
        # the mode table: columns 2 and 3 are the two books, 1 and 4 are prose
        head = m[m.index("cfg16-modetbl"):]
        head = head[:head.index("</thead>")]
        order = re.findall(r'<th class="[^"]*">([^<]+)</th>', head)
        assert order[:4] == ["Parameter", "Intraday", "Delivery", "Scope"], order
        assert 'cfg16-hc">Parameter' not in head and 'cfg16-hc">Scope' not in head


# ─────────────────────────────────────────────────────────────────────────────
# Presentation: the approved design language.
# ─────────────────────────────────────────────────────────────────────────────
class TestPresentation:

    def test_every_rule_is_scoped_to_the_page(self) -> None:
        r"""⚠️ THE EXTRACTOR WAS VACUOUS UNTIL 02-Sep, and MUTATION found it.

        The old pattern was `^([^@\s][^{]*)\{` under MULTILINE, and its `[^{]*`
        crosses newlines: a `/* ... */` comment sitting directly above a rule was
        absorbed into that rule's "selector", and the loop then SKIPPED the whole
        thing as a comment. Almost every rule in this block carries such a
        comment, so an UNSCOPED rule was invisible — proven by unscoping
        `.cfg16-scope` and watching this test stay green.

        ⭐ Comments are stripped FIRST now, and a selector is read as the run of
        non-brace text between a brace (or the file start) and the next `{`, so a
        rule nested inside an `@media` block is checked too.
        """
        rules = _css_selector_rules()
        # ⭐ NON-VACUITY, AND EXACT — ⛔ not a floor. A floor is what let the
        # broken extractor pass: it returned every OTHER rule and still cleared
        # the number I had guessed. Every `{` in the stripped block opens either
        # an `@media` block or a rule, so the rule count is arithmetic:
        block = _css_rules()
        expected = block.count("{") - block.count("@media")
        assert len(rules) == expected, (len(rules), expected)
        selectors = [sel for sel, _ in rules]
        for sel in selectors:
            if not sel or sel.startswith(":root"):
                continue
            assert (".cfg16-page" in sel or "main.content:has(> .cfg16-page)" in sel), sel

    def test_the_13px_readability_floor_holds(self) -> None:
        block = _css_rules()
        px = [int(m) for m in re.findall(r"font-size:\s*(\d+)px", block)]
        assert px, "the block must set font sizes"
        assert min(px) >= 13, min(px)
        assert not re.search(r"font-size:\s*0?\.\d+rem", block)

    def test_no_new_colour_is_introduced(self) -> None:
        """Every colour must be an existing token — ⛔ no literal hex, no rgb()."""
        block = _css_rules()
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)
        assert not re.search(r"\brgba?\(", block)

    def test_nothing_is_hidden_at_any_width(self) -> None:
        """⛔ A column that renders nothing is worse than one behind a scroll —
        the 16-Aug `table-layout: fixed` finding, not repeated here."""
        block = _css_rules()
        assert "display: none" not in block
        assert "table-layout: fixed" not in block
        # ⭐ NON-VACUITY: the scan DOES find the declarations that are there, so
        # a miss above is absence, ⛔ not a stripper that ate the stylesheet.
        assert "table-layout: auto" in block
        assert "overflow-y: auto" in block

    def test_the_five_menu_sections_of_the_artwork_are_all_present(self) -> None:
        m = _markup()
        for heading in ("CONFIGURATION CATEGORIES", "SCORING ENGINE",
                        "SLIPPAGE CONFIGURATION", "MODE-SPECIFIC CONFIGURATION",
                        "GLOBAL LIMITS", "CONFIGURATION HISTORY",
                        "CONFIGURATION COMPARISON", "BROKER COSTS",
                        "SEARCH CONFIGURATION", "EXPORT"):
            assert heading in m, heading

    def test_the_artwork_has_no_chart_and_none_is_invented(self) -> None:
        m = _markup()
        for word in ("<svg", "<canvas", "donut", "pie-", "chart"):
            assert word not in m.lower(), word
