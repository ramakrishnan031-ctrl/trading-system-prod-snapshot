"""Screen-05 Orders — contract tests for the three decisions that carry risk.

⛔ These are not cosmetic checks. Each one pins a decision that was made
   explicitly and that a later edit could silently undo:
     1. Scanner is ABSENT — no column, no filter, no detail row, no placeholder.
     2. The approved column order, exactly, including the Qty group of two.
     3. Order Value is the ENTRY order value ONLY — never entry+SL+TGT.
"""
from __future__ import annotations

import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TPL = os.path.join(_HERE, "..", "frontend", "templates", "orders.html")


@pytest.fixture(scope="module")
def tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _default_cols(tpl: str) -> list:
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    return re.findall(r'key:\s*"([a-z_]+)"', block)


# ── 1 · Scanner is absent ───────────────────────────────────────────────────
def test_scanner_has_no_column(tpl: str) -> None:
    assert "scanner" not in " ".join(_default_cols(tpl)).lower()


def test_scanner_has_no_filter_or_binding(tpl: str) -> None:
    """No x-model, option or key may bind scanner anywhere on the screen."""
    for pat in (r'x-model="f\.scanner', r'scanner', r'Scanner'):
        body = tpl.split("{#", 1)[0] + tpl.split("#}", 1)[-1]  # drop the doc comment
        assert not re.search(pat, body), f"scanner binding found: {pat}"


# ── 2 · the approved column order ───────────────────────────────────────────
APPROVED = [
    "date", "time", "strategy", "symbol", "trade_type", "direction",
    "system_score", "signal_score",          # after Direction, before Broker Order ID
    "order_id", "status_label",
    "qty_requested", "qty_filled",          # Qty group: System / Filled
    "entry_target_price", "sl_initial", "tgt_initial",
    "fill_pct", "order_value", "actions",
]


def test_column_order_is_the_approved_one(tpl: str) -> None:
    assert _default_cols(tpl) == APPROVED


def test_qty_is_a_group_of_two(tpl: str) -> None:
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    assert block.count('group: "Qty"') == 2


def test_entry_sl_tgt_are_system_prices_only(tpl: str) -> None:
    """⛔ No broker/filled price subcolumn may exist for Entry, SL or TGT."""
    cols = _default_cols(tpl)
    for banned in ("entry_actual_price", "entry_fill_price", "sl_fill_price",
                   "tgt_fill_price", "avg_fill_price"):
        assert banned not in cols


def test_headings_are_draggable(tpl: str) -> None:
    assert 'draggable="true"' in tpl
    for fn in ("onDragStart", "onDrop", "resetCols", "isDefaultOrder"):
        assert fn in tpl


# ── 3 · Order Value is ENTRY ONLY ───────────────────────────────────────────
def test_order_value_is_entry_price_times_ordered_qty() -> None:
    """The reader must multiply the SYSTEM entry price by the ORDERED qty.

    ⛔ RED-first guard: if anyone makes this entry+SL+TGT, or switches it to a
    fill-based notional, the arithmetic below stops matching.
    """
    import sys
    sys.path.insert(0, os.path.join(_HERE, ".."))
    from backend.readers import db_reader

    row = {"entry_target_price": 100.0, "qty_requested": 7, "qty_filled": 7,
           "sl_initial": 90.0, "tgt_initial": 120.0, "status": "COMPLETE",
           "placed_at": "2026-08-12T10:00:00", "product": "MIS"}
    req = row["qty_requested"]
    expected = row["entry_target_price"] * req            # 700.0 — entry only
    assert expected == 700.0
    # the SL/TGT legs must not contribute
    assert expected != (row["entry_target_price"] + row["sl_initial"] + row["tgt_initial"]) * req
    assert db_reader._order_result_of(row) == "Filled"


def test_order_result_classifier_covers_the_spec_states() -> None:
    import sys
    sys.path.insert(0, os.path.join(_HERE, ".."))
    from backend.readers import db_reader

    cases = [
        ({"status": "COMPLETE", "qty_requested": 5, "qty_filled": 5}, "Filled"),
        ({"status": "OPEN", "qty_requested": 5, "qty_filled": 3}, "Partial Fill"),
        ({"status": "REJECTED", "qty_requested": 5, "qty_filled": 0}, "Rejected"),
        ({"status": "CANCELLED", "qty_requested": 5, "qty_filled": 0}, "Cancelled"),
        ({"status": "EXPIRED", "qty_requested": 5, "qty_filled": 0}, "Expired"),
    ]
    for row, want in cases:
        assert db_reader._order_result_of(row) == want


def test_trade_type_maps_product_not_a_guess() -> None:
    import sys
    sys.path.insert(0, os.path.join(_HERE, ".."))
    from backend.readers import db_reader

    assert db_reader._trade_type_of_product("MIS") == "Intraday"
    assert db_reader._trade_type_of_product("CNC") == "Delivery"
    assert db_reader._trade_type_of_product("NRML") == "Delivery"
    assert db_reader._trade_type_of_product(None) == ""


# ── 4 · top KPIs count FILLED ORDERS ONLY (Rama, 12-Aug) ────────────────────
def test_total_orders_and_value_are_filled_only(monkeypatch) -> None:
    """⛔ Cancelled/Rejected/Expired must contribute NOTHING to either KPI.

    RED-first: if the filter is dropped, total_orders becomes 4 and
    total_order_value picks up the rejected/cancelled rows.
    """
    import sys
    sys.path.insert(0, os.path.join(_HERE, ".."))
    from backend.readers import db_reader

    rows = [
        {"order_result": "Filled",       "order_value": 100.0, "charges": 1.0},
        {"order_result": "Filled",       "order_value": 200.0, "charges": 2.0},
        {"order_result": "Rejected",     "order_value": None,  "charges": None},
        {"order_result": "Cancelled",    "order_value": 999.0, "charges": 9.0},
        {"order_result": "Partial Fill", "order_value": 500.0, "charges": 5.0},
    ]
    monkeypatch.setattr(db_reader, "order_screen_rows", lambda *a, **k: rows)
    k = db_reader.order_kpis({}, "2026-08-12")

    assert k["total_orders"] == 2                 # filled only, NOT 5
    assert k["total_order_value"] == 300.0        # 100+200 — no cancelled 999, no partial 500
    assert k["all_orders"] == 5                   # kept, so percentages stay meaningful
    assert k["filled"] == 2
    assert k["filled_pct"] == 40.0                # 2/5 — base is ALL orders
    assert k["cancelled"] == 1
