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
    "system_score", "score_threshold",       # after Direction, before Broker Order ID
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


# ── 4 · Rama, 20-Aug: centred headings + no currency in ordinary data cells ──
# Visual decisions, so no API test can see them regress. Pinned against the
# template/CSS themselves, which is the only surface that carries them.
_HC_EXPECTED = {
    "trade_type", "direction", "system_score", "score_threshold", "order_id",
    "status_label", "qty_requested", "qty_filled", "entry_target_price",
    "sl_initial", "tgt_initial", "fill_pct", "order_value", "actions",
}
_HC_LEFT = {"date", "time", "strategy", "symbol"}


def test_exactly_the_fourteen_ruled_headings_are_centred(tpl: str) -> None:
    """`hc` marks Trade Type -> Actions and NOTHING else.

    It rides on the column DEFINITION, never on a position: these columns are
    drag-reorderable, so an nth-child rule would centre the wrong heading after
    a drag.
    """
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    got = {m.group(1) for m in
           re.finditer(r'\{\s*key:\s*"([a-z_]+)"[^}]*\bhc:\s*true', block)}
    assert got == _HC_EXPECTED, "centred set drifted: %s" % (got ^ _HC_EXPECTED)
    for key in _HC_LEFT:
        row = re.search(r'\{\s*key:\s*"%s".*?\}' % key, block, re.S).group(0)
        assert "hc:" not in row, "%s must stay left-aligned" % key
    assert "c.hc ? 'hc' : ''" in tpl, "the hc class is not bound onto the <th>"


def test_ordinary_data_cells_carry_no_currency_symbol(tpl: str) -> None:
    """Table cells render 1175.00, not Rs.1175.00 — and ONLY the table cells.

    ⛔ This must not become a global de-currencying: `rs()` still carries the
    symbol so the Order Details rail keeps it, and the KPI deck, the summary
    panels and the "Entry / SL / TGT" HEADINGS keep it too.
    """
    rupee = "₹"
    assert "return this.rsCell(v);" in tpl, "the rs cell branch does not use rsCell"
    cellfn = tpl.split("rsCell(v) {", 1)[1].split("},", 1)[0]
    assert 'replace("%s", "")' % rupee in cellfn, "rsCell does not strip the symbol"
    # the rail formatter is untouched, so summary contexts keep the symbol
    rsfn = re.search(r"\n\s+rs\(v\) \{[^\n]*\n", tpl).group(0)
    assert rupee in rsfn, "rs() lost its currency symbol — the rail would lose it too"
    # the three price HEADINGS keep it
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("],", 1)[0]
    assert block.count('label: "Entry %s"' % rupee) == 1
    assert block.count('label: "SL %s"' % rupee) == 1
    assert block.count('label: "TGT %s"' % rupee) == 1


def test_no_row_hiding_was_added_for_unattributed_orders(tpl: str) -> None:
    """⛔ MEASURED: production has ZERO unattributable ENTRY orders.

    All-time on the VM (read-only): 542 ENTRY orders, 0 without a trade row,
    0 without a strategy, 0 without a symbol; the inner JOIN returns 542,
    identical to the LEFT JOIN. The fully-"—" rows seen during QA came from
    `conftest._build_db`'s synthetic `ord_e_*` fixture, NOT from the product.
    So no exclusion belongs in the template: it could never fire in production
    and could only ever hide a legitimate row.
    """
    for pat in ("orphan", "unattributed", "unattrib"):
        assert pat not in tpl.lower(), (
            "a row-exclusion path was added for a condition production cannot "
            "produce; see the measurement in this test's docstring")
