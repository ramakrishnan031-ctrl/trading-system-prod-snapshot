"""Screen-06 Positions — contract tests for the decisions that carry risk.

⛔ These are not cosmetic checks. Each pins a decision made explicitly on
   12-Aug-2026 that a later edit could silently undo:
     1. Scanner is ABSENT — no column, no filter, no detail row, no export column.
     2. The approved column order, including the four System/Broker pairs.
     3. SL/TGT's second column is BROKER-STANDING and is NEVER called "Filled" —
        there is no filled SL/TGT execution price in the schema at all.
     4. Position quantity is the BROKER-FILLED quantity, not the system's.
     5. LTP / Current Value / Unrealized / MTM / Current RR stay honestly
        UNAVAILABLE — ⛔ never zero, never blank, never invented.
     6. Export represents the FILTERED result set.
"""
from __future__ import annotations

import os
import re

import pytest

from backend.readers import db_reader

_HERE = os.path.dirname(os.path.abspath(__file__))
_TPL = os.path.join(_HERE, "..", "frontend", "templates", "positions.html")


@pytest.fixture(scope="module")
def tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _default_cols(tpl: str) -> list:
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    return re.findall(r'key:\s*"([a-z_]+)"', block)


def _body(tpl: str) -> str:
    """Template minus the leading {# ... #} design comment (which legitimately
    NAMES the banned things in order to record that they are banned)."""
    return tpl.split("#}", 1)[-1]


# ── 1 · Scanner is absent ───────────────────────────────────────────────────
def test_scanner_has_no_column(tpl: str) -> None:
    assert "scanner" not in " ".join(_default_cols(tpl)).lower()


def test_scanner_appears_nowhere_on_the_screen(tpl: str) -> None:
    """⛔ No filter, binding, heading or label may mention Scanner."""
    assert not re.search(r"scanner", _body(tpl), re.IGNORECASE)


def test_scanner_is_not_an_export_column() -> None:
    from backend.api import trading
    labels = " ".join(lbl for lbl, _ in trading._POS_EXPORT_COLS).lower()
    keys = " ".join(k for _, k in trading._POS_EXPORT_COLS).lower()
    assert "scanner" not in labels and "scanner" not in keys


# ── 2 · the approved column order ───────────────────────────────────────────
# The user's spreadsheet ("position screen.xlsx", 12-Aug-2026) is the authority
# for this list. ⛔ Capital Used / Risk / Highest Profit / Highest Drawdown /
# Position Age are deliberately NOT table columns — they live in the detail card,
# which is where the spreadsheet leaves them.
APPROVED = [
    # common head, identical to Screens 04/05 — ⛔ no Scanner between them
    "date", "time", "strategy", "symbol", "trade_type", "direction",
    "system_score", "signal_score",
    "position_status",
    "qty_system", "qty_position",                       # Qty group
    "entry_target_price", "entry_actual_price",         # Entry group: System / Filled
    "sl_initial", "sl_broker",                          # SL group:    System / Broker
    "tgt_initial", "tgt_broker",                        # TGT group:   System / Broker
    "sl_points", "tgt_points",                          # per-share ₹ distances
    "rr_configured",                                    # strategy.yaml, not derived
    "ltp", "unrealised",                                # unavailable in this build
    "actions",
]


def test_column_order_is_the_approved_one(tpl: str) -> None:
    assert _default_cols(tpl) == APPROVED


def test_scores_participate_in_the_movable_mechanism(tpl: str) -> None:
    """System Score and Signal Score are ordinary members of DEFAULT_COLS, so
    they are dragged and persisted by the same mechanism as every other head."""
    cols = _default_cols(tpl)
    assert "system_score" in cols and "signal_score" in cols


def test_headings_are_draggable(tpl: str) -> None:
    for hook in ('draggable="true"', "@dragstart=", "@drop.prevent=", "onDrop(", "saveCols("):
        assert hook in tpl, f"drag-to-reorder hook missing: {hook}"


def test_price_pairs_are_groups_of_two(tpl: str) -> None:
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    for group in ('group: "Qty"', 'group: "Entry ₹"', 'group: "SL ₹"', 'group: "TGT ₹"'):
        assert block.count(group) == 2, f"{group} must pair exactly two columns"


# ── 3 · SL/TGT second column is BROKER, never "Filled" ──────────────────────
def test_sl_and_tgt_second_column_is_labelled_broker(tpl: str) -> None:
    """⛔ THE TERMINOLOGY IS THE POINT (Rama, 12-Aug). avg_fill_price is NULL on
    every order ever placed, so a 'Filled' SL/TGT would name a value that does
    not exist. Entry legitimately HAS a filled price and keeps that word."""
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    for key in ("sl_broker", "tgt_broker"):
        line = next(l for l in block.splitlines() if f'key: "{key}"' in l)
        assert 'label: "Broker"' in line, f"{key} must be labelled Broker, not Filled"
    entry = next(l for l in block.splitlines() if 'key: "entry_actual_price"' in l)
    assert 'label: "Filled"' in entry, "Entry's second column IS a real fill price"


def test_no_filled_sl_or_tgt_anywhere(tpl: str) -> None:
    """⛔ No LABEL and no DATA BINDING may present a filled SL/TGT.

    ⚠️ Scoped to labels and bindings on purpose: the SL/TGT tab EXPLAINS in prose
    that `avg_fill_price` is null on every order, and that sentence is the whole
    reason an operator can trust the Broker column. Banning the word outright
    would delete the explanation along with the defect.
    """
    body = _body(tpl)
    for banned in ("SL (Filled)", "TGT (Filled)", "SL (filled)", "TGT (filled)"):
        assert banned not in body, f"fabricated filled SL/TGT label: {banned}"
    for banned in ("sl_fill", "tgt_fill", "avg_fill_price"):
        assert banned not in _default_cols(tpl), f"fabricated column: {banned}"
    # and it may never be a live binding, only prose
    assert not re.search(r'x-(text|html)="[^"]*avg_fill_price', body)


def test_export_names_sl_tgt_as_broker() -> None:
    from backend.api import trading
    labels = [lbl for lbl, _ in trading._POS_EXPORT_COLS]
    assert "SL (Broker)" in labels and "TGT (Broker)" in labels
    assert "SL (Filled)" not in labels and "TGT (Filled)" not in labels


# ── 4 · quantity is the broker-filled one ───────────────────────────────────
def test_position_qty_is_the_broker_filled_qty(gui_config, today) -> None:
    """qty_position must come from trades.qty_filled (broker), qty_system from
    qty_planned. ⛔ They are never the same field under two names."""
    rows = db_reader.position_screen_rows(gui_config, today)
    assert rows, "fixture must produce positions or this test is vacuous"
    for r in rows:
        assert r["qty_position"] == r["qty_filled"]
        assert r["qty_system"] == r["qty_planned"]


# ── 5 · broker-standing SL/TGT come from the real leg orders ────────────────
def test_broker_exits_read_the_real_leg_orders(gui_config, today) -> None:
    rows = db_reader.position_screen_rows(gui_config, today)
    ids = [r["trade_id"] for r in rows]
    bx = db_reader.position_broker_exits(gui_config, ids)
    # trd_c1 has an SL leg at trigger 989.5; trd_c4 a TGT leg at limit 1015.0.
    assert bx["trd_c1"]["sl_broker"] == pytest.approx(989.5)
    assert bx["trd_c4"]["tgt_broker"] == pytest.approx(1015.0)


def test_broker_exit_mismatch_is_visible_not_smoothed(gui_config, today) -> None:
    """⭐ NON-VACUOUS: the fixture's broker SL (989.5) deliberately DIFFERS from
    the system SL (990.0). If a future edit ever defaulted broker to system,
    this equality would appear and the test goes red."""
    rows = {r["trade_id"]: r for r in db_reader.position_screen_rows(gui_config, today)}
    bx = db_reader.position_broker_exits(gui_config, list(rows))
    assert bx["trd_c1"]["sl_broker"] != rows["trd_c1"]["sl_initial"]


def test_a_trade_without_exit_legs_returns_nothing(gui_config, today) -> None:
    """Two-state: absent legs yield no key at all, so the UI renders an
    em-dash. ⛔ Never a zero and never the system value."""
    bx = db_reader.position_broker_exits(gui_config, ["trd_does_not_exist"])
    assert bx == {}


# ── 6 · unavailable stays unavailable ───────────────────────────────────────
def test_kpis_report_mtm_as_unavailable(gui_config, today) -> None:
    """⛔ current_mtm must be None with a stated reason — ⛔ never 0.0, which
    would read as a measured flat P&L."""
    k = db_reader.position_kpis(gui_config, today)
    assert k["current_mtm"] is None
    assert k["current_mtm_reason"]
    assert k["realized_pnl_today"] is not None, "realized P&L IS available and differs"


def test_summary_declares_mtm_panel_unavailable(gui_config, today) -> None:
    s = db_reader.position_summary(gui_config, today)
    assert s["mtm"]["available"] is False and s["mtm"]["reason"]
    # the other three panels are real
    assert s["capital"]["total"] is not None
    assert s["distribution"]["total"] == s["distribution"]["long"] + s["distribution"]["short"]
    assert sum(s["status_breakdown"].values()) == s["status_total"]


def test_api_publishes_the_unavailable_contract(client, today) -> None:
    r = client.get(f"/api/positions/screen?date={today}")
    assert r.status_code == 200
    body = r.get_json()
    for f in ("ltp", "current_value", "unrealized_pnl", "mtm", "current_rr"):
        assert f in body["unavailable"]["fields"]
    assert body["unavailable"]["reason"]


def test_only_the_requested_live_columns_exist(tpl: str) -> None:
    """The spreadsheet asks for LTP and Unrealised BY NAME, so they are columns —
    rendered as an explicit unavailable marker (see the renderer test below).

    ⛔ The OTHER live-derived values are still not columns: Current Value,
    Unrealized %, MTM and Current RR were never requested in the table, and an
    always-empty column implies the value exists and merely happened to be blank.
    """
    cols = _default_cols(tpl)
    assert "ltp" in cols and "unrealised" in cols
    for banned in ("current_value", "unrealized_pnl", "unrealized_pct",
                   "mtm", "current_rr"):
        assert banned not in cols


# ── 7 · derived values are honest ───────────────────────────────────────────
def test_expected_rr_is_none_when_risk_is_not_positive() -> None:
    assert db_reader._expected_rr(100, 100, 110, "LONG") is None   # zero risk
    assert db_reader._expected_rr(100, 110, 120, "LONG") is None   # inverted
    assert db_reader._expected_rr(None, 90, 110, "LONG") is None
    assert db_reader._expected_rr(100, 90, 120, "LONG") == pytest.approx(2.0)


def test_points_are_per_share_and_direction_aware() -> None:
    """⛔ PER SHARE, never multiplied by qty (spreadsheet notes 2/3), and
    direction-aware so a correctly-placed level is a POSITIVE distance."""
    # LONG entry 100, sl 90, tgt 120
    assert db_reader._level_points(100, 90, "LONG", True) == pytest.approx(10.0)
    assert db_reader._level_points(100, 120, "LONG", False) == pytest.approx(20.0)
    # SHORT entry 100, sl 110, tgt 80 — same magnitudes, mirrored levels
    assert db_reader._level_points(100, 110, "SHORT", True) == pytest.approx(10.0)
    assert db_reader._level_points(100, 80, "SHORT", False) == pytest.approx(20.0)
    # ⛔ absent operand is absent, NOT zero
    assert db_reader._level_points(None, 90, "LONG", True) is None
    assert db_reader._level_points(100, None, "LONG", True) is None


def test_points_come_from_system_levels_not_broker(gui_config, today) -> None:
    """Section E: the points columns describe what the SYSTEM intended."""
    rows = {r["trade_id"]: r for r in db_reader.position_screen_rows(gui_config, today)}
    r = rows["trd_c1"]          # entry 1000, sl_initial 990, LONG
    assert r["sl_points"] == pytest.approx(10.0)
    # the broker SL is 989.5 — if points were computed from it we would see 10.5
    assert r["sl_points"] != pytest.approx(10.5)


def test_unrealised_formula_is_right_but_unreachable_without_ltp() -> None:
    """⭐ The arithmetic is pinned NOW so it is correct the day a live-price
    source exists. ⛔ Until then every caller passes ltp=None and gets None."""
    # LONG: (ltp - filled entry) * qty
    assert db_reader.position_unrealised(100.0, 110.0, 5, "LONG") == pytest.approx(50.0)
    # SHORT: (filled entry - ltp) * qty
    assert db_reader.position_unrealised(100.0, 90.0, 5, "SHORT") == pytest.approx(50.0)
    # losses stay negative (the UI colours on the sign)
    assert db_reader.position_unrealised(100.0, 90.0, 5, "LONG") == pytest.approx(-50.0)
    # ⛔ no LTP ⇒ no number
    assert db_reader.position_unrealised(100.0, None, 5, "LONG") is None
    assert db_reader.position_unrealised(None, 110.0, 5, "LONG") is None
    assert db_reader.position_unrealised(100.0, 110.0, 0, "LONG") is None


def test_rows_never_carry_a_fabricated_ltp(gui_config, today) -> None:
    """⛔ THE CENTRAL DATA RULE for these two columns: no live source exists, so
    ltp and unrealised must be None on every row — ⛔ never 0.0, and ⛔ never
    quietly back-filled from a system or last-known price."""
    rows = db_reader.position_screen_rows(gui_config, today)
    assert rows
    for r in rows:
        assert r["ltp"] is None
        assert r["unrealised"] is None


def test_unrecognised_exit_reason_never_becomes_sl_or_tgt() -> None:
    """⛔ A reason we do not understand resolves to the neutral 'Closed'.
    GTT_EXIT is a MECHANISM and must not be read as an SL or TGT hit."""
    assert db_reader._position_status_of({"status": "CLOSED", "exit_reason": "GTT_EXIT"}) == "Closed"
    assert db_reader._position_status_of({"status": "CLOSED", "exit_reason": "???"}) == "Closed"
    assert db_reader._position_status_of({"status": "CLOSED", "exit_reason": "SL_HIT"}) == "SL Hit"
    assert db_reader._position_status_of({"status": "CLOSED", "exit_reason": "TGT_HIT"}) == "TGT Hit"
    assert db_reader._position_status_of({"status": "OPEN"}) == "Open"
    assert db_reader._position_status_of({"status": "PARTIAL"}) == "Partial Exit"


def test_excursions_absent_means_absent_not_zero(gui_config, today) -> None:
    """⛔ A trade with no reconstructed excursion must not read as 0% drawdown."""
    exc = db_reader.position_excursions(gui_config, ["trd_c1", "trd_o1"])
    assert exc["trd_c1"]["mae_pct"] == pytest.approx(-0.8)   # seeded
    assert "trd_o1" not in exc                                # absent, not zero


# ── 8 · export respects the filters ─────────────────────────────────────────
def test_export_applies_the_same_filters_as_the_table() -> None:
    from backend.api import trading
    rows = [
        {"strategy": "a", "symbol": "AAA", "trade_type": "Intraday",
         "direction": "LONG", "position_status": "Open"},
        {"strategy": "b", "symbol": "BBB", "trade_type": "Delivery",
         "direction": "SHORT", "position_status": "Closed"},
    ]
    f = {"strategy": "a", "symbol": "", "trade_type": "", "direction": "",
         "position_status": ""}
    assert trading._apply_position_filters(rows, f) == [rows[0]]
    f2 = {"strategy": "", "symbol": "", "trade_type": "", "direction": "SHORT",
          "position_status": ""}
    assert trading._apply_position_filters(rows, f2) == [rows[1]]
    empty = {"strategy": "", "symbol": "", "trade_type": "", "direction": "",
             "position_status": ""}
    assert trading._apply_position_filters(rows, empty) == rows


def test_export_endpoint_returns_a_real_xlsx(client, today) -> None:
    r = client.get(f"/api/export/positions?date={today}")
    assert r.status_code == 200
    assert r.data[:2] == b"PK", "must be a real xlsx (zip magic), not a stub"


def test_export_filter_actually_narrows_the_sheet(client, today) -> None:
    """⭐ Could go red: an export ignoring its filters would return the same
    bytes for both requests."""
    wide = client.get(f"/api/export/positions?date={today}")
    narrow = client.get(f"/api/export/positions?date={today}&position_status=Open")
    assert wide.status_code == narrow.status_code == 200
    assert wide.data != narrow.data


# ── 9 · the page still renders ──────────────────────────────────────────────
def test_positions_page_renders(client) -> None:
    r = client.get("/positions")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "positionsPage()" in html
    assert "pos-page" in html and "ord-page" in html   # reuse of Screen-05's language


# ── 10 · spreadsheet spec: R:R, LTP/Unrealised, Action ──────────────────────
def test_rr_is_strategy_configured_not_price_derived(gui_config, today, client) -> None:
    """Spreadsheet note 4: "R:R — refers to ratio as per each strategy.yaml".

    ⛔ The table's R:R must be the CONFIGURED ratio. The fixture strategies have
    no `tgt_risk_reward`, so it must be None — ⛔ NOT silently replaced by the
    price-derived ratio, which the same rows DO have.
    """
    body = client.get(f"/api/positions/screen?date={today}").get_json()
    rows = body["rows"]
    assert rows
    assert all(r["rr_configured"] is None for r in rows), \
        "no fixture strategy configures tgt_risk_reward, so R:R must be unavailable"
    # ⭐ and the price-derived one IS present — proving the table is not just
    # showing that value under a different name.
    assert any(r["expected_rr"] is not None for r in rows)


def test_rr_is_read_when_the_strategy_configures_it(tmp_path, gui_config, today) -> None:
    """⭐ NON-VACUOUS COUNTERPART: add tgt_risk_reward to a strategy file and the
    value must appear. Without this, the test above could pass on a broken read."""
    import os
    import yaml
    from backend.api import trading

    sdir = os.path.join(gui_config["paths"]["config_dir"], "strategies")
    path = os.path.join(sdir, "gap_fade_long.yaml")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["tgt_risk_reward"] = 2.0
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)

    rows = trading._position_rows_enriched(gui_config, today)
    gap = [r for r in rows if r["strategy"] == "gap_fade_long"]
    assert gap, "fixture must have gap_fade_long positions"
    assert all(r["rr_configured"] == pytest.approx(2.0) for r in gap)
    # a strategy WITHOUT the key stays unavailable — ⛔ no global default leaks in
    other = [r for r in rows if r["strategy"] == "vwap_bounce_long"]
    assert other and all(r["rr_configured"] is None for r in other)


def test_rr_is_not_reversed_for_shorts(tmp_path, gui_config, today) -> None:
    """Section F: ⛔ do not reverse the configured ratio for short positions."""
    import os
    import yaml
    from backend.api import trading

    sdir = os.path.join(gui_config["paths"]["config_dir"], "strategies")
    for name in ("gap_fade_long", "vwap_bounce_long"):
        p = os.path.join(sdir, f"{name}.yaml")
        with open(p, encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        d["tgt_risk_reward"] = 2.5
        with open(p, "w", encoding="utf-8") as fh:
            yaml.safe_dump(d, fh, sort_keys=False)
    rows = trading._position_rows_enriched(gui_config, today)
    vals = [r["rr_configured"] for r in rows if r["strategy"] in
            ("gap_fade_long", "vwap_bounce_long")]
    assert vals, "fixture must have positions on both strategies"
    assert all(v == pytest.approx(2.5) for v in vals), \
        "same configured ratio regardless of direction"


def test_ltp_and_unrealised_render_unavailable_not_zero(tpl: str) -> None:
    """⛔ The cell renderer must emit an explicit marker, ⛔ never a 0 or a blank
    that would read as a measured value."""
    body = _body(tpl)
    assert 'c.kind === "ltp"' in body and 'c.kind === "unreal"' in body
    assert "pos-nolive" in body
    # colour-by-sign is present for the day a real value arrives
    for cls in ("v-pos", "v-neg", "v-zero"):
        assert cls in body


def test_action_column_exists_and_cannot_execute(tpl: str) -> None:
    """Section G: the Action control is UI flow only. The dashboard has no write
    path (its only POST route is /login and the DB is read-only), so the dialog
    must say so and the confirm must be disabled. ⛔ No fabricated close."""
    body = _body(tpl)
    assert 'class="pos-act"' in body, "each row needs an Action control"
    assert "pos-modal" in body, "Action must open a dialog"
    assert "openAction(" in body and "rowClick(" in body
    assert "cannot place the order" in body, "the dialog must state it cannot execute"
    # the confirm button is disabled, and the CMP option too (no live price)
    assert re.search(r'btn-primary"[^>]*disabled', body), "confirm must be disabled"
    assert re.search(r'value="cmp"[^>]*disabled', body), "CMP needs a live price it lacks"


def test_no_write_route_exists_in_the_whole_dashboard() -> None:
    """⭐ The guarantee the dialog's wording rests on, asserted rather than
    assumed: /login is the ONLY POST endpoint anywhere in the app."""
    import os
    import re as _re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    posts = []
    for dirpath, _d, files in os.walk(os.path.join(root, "backend")):
        for name in files:
            if not name.endswith(".py"):
                continue
            p = os.path.join(dirpath, name)
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for n, line in enumerate(fh, 1):
                    if _re.search(r'methods\s*=\s*\[[^\]]*["\']POST["\']', line):
                        posts.append(f"{os.path.relpath(p, root)}:{n}")
    # login + logout are the only two, and both are SESSION routes — neither
    # writes trading data, and there is no order path anywhere.
    assert all("auth.py" in p for p in posts), \
        f"a write route appeared outside auth — the dialog's claim must be revisited: {posts}"
    assert len(posts) == 2, f"expected exactly login+logout, got: {posts}"
