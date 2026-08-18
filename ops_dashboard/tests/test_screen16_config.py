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
                    "strategies", "broker_costs", "comparison", "history",
                    "search_categories", "changed_by_note"):
            assert key in d, f"missing block: {key}"

    def test_the_six_artwork_kpis_are_present_in_order(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert [k["key"] for k in d["kpis"]] == [
            "trade_mode", "broker", "max_open_positions", "max_daily_trades",
            "min_pass_score", "daily_loss_limit"]
        assert [k["label"] for k in d["kpis"]] == [
            "Trade Mode", "Broker", "Max Open Positions", "Max Daily Trades",
            "Minimum Pass Score", "Daily Loss Limit"]

    def test_the_twelve_artwork_categories_are_present_in_order(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert [c["label"] for c in d["categories"]] == [
            "System", "Trading Hours", "Capital", "Risk", "Position Sizing",
            "Scoring", "Slippage", "Strategies", "Alerts", "Broker",
            "Scanners", "Advanced"]

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

    def test_enabled_is_a_read_only_pill_not_a_switch(self) -> None:
        m = _markup()
        assert "cfg16-pill" in m                      # the read-only state pill
        assert "ctl-sw" not in m                      # Screen 17's switch class
        assert 'type="checkbox"' not in m
        # The ONLY buttons are view-state: tabs, popular-search chips, view-all.
        # ⚠️ `(?<=\s)` is load-bearing: Alpine's `:class="…"` binding also ends
        # in `class="`, so a looser pattern reads the ternary as a class list.
        classes = re.findall(r'<button[^>]*?(?<=\s)class="([^"]+)"', m)
        for cls in classes:
            assert any(k in cls for k in ("cfg16-tab", "cfg16-chip", "cfg16-more")), cls

    def test_the_page_talks_to_exactly_the_two_read_only_endpoints(self) -> None:
        m = _markup()
        endpoints = set(re.findall(r'"(/api/[a-z0-9/\-]+)"', m))
        assert endpoints == {"/api/config/screen", "/api/export/config-screen"}


# ─────────────────────────────────────────────────────────────────────────────
# ⛔ NO SCANNER IDENTITY. Scanner→strategy is 1:1.
# ─────────────────────────────────────────────────────────────────────────────
class TestScannerIsNotAnIdentity:

    def test_strategy_rows_carry_no_scanner_field(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        assert d["strategies"], "fixture must configure strategies"
        for s in d["strategies"]:
            assert "scanner" not in s
            assert set(s) == {"name", "label", "enabled", "trade_type",
                              "status", "direction"}

    def test_the_template_has_no_scanner_column_and_no_mapping_panel(self) -> None:
        m = _markup()
        assert "SCANNER MAPPING" not in m
        assert "Scanner URL" not in m
        assert "chartink" not in m.lower()
        # ⭐ NON-VACUITY: the same search over the same text DOES find the four
        # columns that must be there, so a miss above is absence, not a broken
        # search.
        for col in ("Strategy Name", "Enabled", "Trade Type", "Status"):
            assert col in m, col

    def test_the_scanners_category_states_the_relationship_without_listing_scanners(
            self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        cat = next(c for c in d["categories"] if c["key"] == "scanners")
        params = {r["parameter"] for r in cat["rows"]}
        assert params == {"scanners_mapped", "distinct_strategies_targeted",
                          "relationship"}
        # ⛔ no row is named after a scanner
        names = {r["parameter"] for r in cat["rows"]}
        assert not any(n.endswith("_long") or n.endswith("_short") for n in names)


# ─────────────────────────────────────────────────────────────────────────────
# ⛔ NOTHING IS HARD-CODED FROM THE ARTWORK.
# ─────────────────────────────────────────────────────────────────────────────
class TestValuesComeFromConfiguration:

    def test_risk_values_are_the_fixtures_own_not_the_artworks(self, client) -> None:
        d = client.get("/api/config/screen").get_json()
        rows = {r["label"]: r["value"] for r in d["risk"]}
        # the fixture snapshot: max_open_positions 5, max_daily_trades 10,
        # max_consecutive_losses 5, daily_loss 3%, sector 40%
        assert rows["Max Open Positions"] == 5          # artwork draws 10
        assert rows["Max Daily Trades"] == 10           # artwork draws 50
        assert rows["Max Consecutive Losses"] == 5      # artwork draws 3
        assert rows["Daily Loss Limit %"] == "3.00%"    # artwork draws 5.00%
        assert rows["Sector Exposure %"] == "40.00%"    # artwork draws 25.00%

    def test_the_artworks_mock_numbers_do_not_appear_anywhere(self, client) -> None:
        """⭐ The reverse of the test above, and the one that would catch a value
        copied off the screenshot."""
        d = client.get("/api/config/screen").get_json()
        kpis = {k["key"]: k["value"] for k in d["kpis"]}
        assert kpis["max_open_positions"] != 10
        assert kpis["max_daily_trades"] != 50
        assert kpis["daily_loss_limit"] != "5.00%"

    def test_a_percentage_carries_its_base(self, client) -> None:
        """⛔ A bare percentage is not a number. The daily-loss KPI names the
        base its limit is a percentage OF."""
        d = client.get("/api/config/screen").get_json()
        loss = next(k for k in d["kpis"] if k["key"] == "daily_loss_limit")
        assert "opening capital" in (loss["sub"] or "")
        risk = {r["label"]: r for r in d["risk"]}
        assert risk["Daily Loss Limit %"]["note"] == "of opening capital"

    def test_capital_allocations_are_percentages_not_rupees(self, client) -> None:
        """⛔ NO rupee capital value exists in configuration — the artwork's
        '₹5,00,000' has no source and must not be manufactured."""
        d = client.get("/api/config/screen").get_json()
        rows = {r["label"]: r for r in d["capital"]}
        assert rows["Intraday Allocation"]["value"] == "70.00%"
        assert rows["Delivery Allocation"]["value"] == "30.00%"
        for r in d["capital"]:
            assert "₹" not in str(r["value"])

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
                                 "Strategies", "Broker Costs", "Comparison",
                                 "History"]
        rows = list(wb["Risk"].values)
        assert rows[0] == ("Parameter", "Value", "Note")
        assert any(r and r[0] == "Max Daily Trades" and r[1] == 10 for r in rows)

    def test_the_strategies_sheet_has_no_scanner_column(self, client) -> None:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(client.get("/api/export/config-screen").data))
        header = list(wb["Strategies"].values)[0]
        assert header == ("Strategy", "Enabled", "Trade Type", "Status")

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
            client.get("/api/export/config-screen?q=consecutive").data))
        assert full.sheetnames == filt.sheetnames        # sheets are never dropped
        full_rows = len(list(full["Risk"].values))
        filt_rows = len(list(filt["Risk"].values))
        assert 1 < filt_rows < full_rows                 # header + the match only
        assert any(r and "Consecutive" in str(r[0]) for r in filt["Risk"].values)

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
# Presentation: the approved design language.
# ─────────────────────────────────────────────────────────────────────────────
class TestPresentation:

    def test_every_rule_is_scoped_to_the_page(self) -> None:
        block = _css_block()
        selectors = re.findall(r"^([^@\s][^{]*)\{", block, flags=re.MULTILINE)
        for sel in selectors:
            sel = sel.strip()
            if not sel or sel.startswith(("/*", "}", ":root")):
                continue
            assert (".cfg16-page" in sel or "main.content:has(> .cfg16-page)" in sel), sel

    def test_the_13px_readability_floor_holds(self) -> None:
        block = _css_block()
        px = [int(m) for m in re.findall(r"font-size:\s*(\d+)px", block)]
        assert px, "the block must set font sizes"
        assert min(px) >= 13, min(px)
        assert not re.search(r"font-size:\s*0?\.\d+rem", block)

    def test_no_new_colour_is_introduced(self) -> None:
        """Every colour must be an existing token — ⛔ no literal hex, no rgb()."""
        block = _css_block()
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)
        assert not re.search(r"\brgba?\(", block)

    def test_nothing_is_hidden_at_any_width(self) -> None:
        """⛔ A column that renders nothing is worse than one behind a scroll —
        the 16-Aug `table-layout: fixed` finding, not repeated here."""
        block = _css_block()
        assert "display: none" not in block
        assert "table-layout: fixed" not in block

    def test_the_five_menu_sections_of_the_artwork_are_all_present(self) -> None:
        m = _markup()
        for heading in ("CONFIGURATION CATEGORIES", "SCORING ENGINE",
                        "SLIPPAGE CONFIGURATION", "STRATEGY CONFIGURATION",
                        "CONFIGURATION HISTORY", "CONFIGURATION COMPARISON",
                        "BROKER COSTS", "SEARCH CONFIGURATION", "EXPORT"):
            assert heading in m, heading

    def test_the_artwork_has_no_chart_and_none_is_invented(self) -> None:
        m = _markup()
        for word in ("<svg", "<canvas", "donut", "pie-", "chart"):
            assert word not in m.lower(), word
