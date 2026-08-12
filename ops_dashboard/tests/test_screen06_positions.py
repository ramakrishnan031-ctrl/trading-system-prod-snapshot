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
APPROVED = [
    # common head, identical to Screens 04/05 — ⛔ no Scanner between them
    "date", "time", "strategy", "symbol", "trade_type", "direction",
    "system_score", "signal_score",
    "position_status",
    "qty_system", "qty_position",                       # Qty group
    "entry_target_price", "entry_actual_price",         # Entry group: System / Filled
    "sl_initial", "sl_broker",                          # SL group:    System / Broker
    "tgt_initial", "tgt_broker",                        # TGT group:   System / Broker
    "sl_dist_pct", "tgt_dist_pct", "expected_rr",
    "capital_used", "risk_amount",
    "mfe_pct", "mae_pct",
    "pos_age", "actions",
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


def test_no_live_price_column_exists(tpl: str) -> None:
    """⛔ LTP / Current Value / Unrealized / MTM must not be table columns. An
    always-empty column implies the value exists and merely happened to be
    blank — the screen says WHY instead."""
    cols = _default_cols(tpl)
    for banned in ("ltp", "current_value", "unrealized_pnl", "unrealized_pct",
                   "mtm", "current_rr"):
        assert banned not in cols


# ── 7 · derived values are honest ───────────────────────────────────────────
def test_expected_rr_is_none_when_risk_is_not_positive() -> None:
    assert db_reader._expected_rr(100, 100, 110, "LONG") is None   # zero risk
    assert db_reader._expected_rr(100, 110, 120, "LONG") is None   # inverted
    assert db_reader._expected_rr(None, 90, 110, "LONG") is None
    assert db_reader._expected_rr(100, 90, 120, "LONG") == pytest.approx(2.0)


def test_pct_from_entry_uses_entry_as_its_base() -> None:
    assert db_reader._pct_from_entry(100, 90) == pytest.approx(10.0)
    assert db_reader._pct_from_entry(0, 90) is None
    assert db_reader._pct_from_entry(None, 90) is None


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
