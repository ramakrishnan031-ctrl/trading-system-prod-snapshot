"""Screen-07 Trade Explorer — contract tests (14-Aug-2026).

The screen's whole reason to exist is that it separates quantities other screens
blur together, so most of these tests are about a quantity NOT being confused
with its neighbour:

  * ROI is on COMMITTED capital (margin_reserved), never the leveraged notional
  * R-multiple (achieved) is never printed as R:R (planned)
  * a FILLED SL/TGT price appears only when the trade's own exit_reason names
    that leg — and is never inferred from the standing broker value
  * "Signal Score" is retired; System Score (achieved) and Score Threshold
    (required) are two different columns

⛔ Several of these are written so they can FAIL: the fixture seeds three
different R:R values plus one NULL, and a mismatching SL/TGT pair, so a reader
that returned a constant, or that copied the broker value into Filled, goes red.
"""
from __future__ import annotations

import io
import os
import re

import pytest
from openpyxl import load_workbook

from backend.api import trading
from backend.readers import db_reader
from conftest import TODAY, YDAY


# ─────────────────────────────────────────────────────────────────────────────
# Pure arithmetic — the three quantities that must not be confused
# ─────────────────────────────────────────────────────────────────────────────
def test_roi_is_on_committed_capital_not_notional():
    """ROI = net / margin_reserved. ⭐ The notional is deliberately NOT the base:
    with 5x intraday leverage the same rupees would report a fifth of the return.
    """
    # net 200 on 5,000 of reserved margin = 4.00%
    assert db_reader.roi_pct(200.0, 5000.0) == 4.00
    # the SAME trade against a 25,000 notional would read 0.80% — the number this
    # function must never produce.
    assert db_reader.roi_pct(200.0, 25000.0) == 0.80
    assert db_reader.roi_pct(200.0, 5000.0) != db_reader.roi_pct(200.0, 25000.0)


def test_roi_is_none_not_zero_when_it_cannot_be_computed():
    """⛔ A zero would read as 'this trade returned nothing'."""
    assert db_reader.roi_pct(None, 5000.0) is None      # trade never closed
    assert db_reader.roi_pct(200.0, None) is None       # no margin recorded
    assert db_reader.roi_pct(200.0, 0) is None          # zero margin: undefined
    assert db_reader.roi_pct(200.0, -1.0) is None


def test_r_multiple_is_a_different_quantity_from_rr():
    """A trade PLANNED at 1.5:1 that stops out scores about -1R. ⛔ The planned
    ratio and the achieved multiple can never be substituted for one another."""
    assert db_reader.r_multiple(-100.0, 100.0) == -1.0   # stopped out at 1R risk
    assert db_reader.r_multiple(150.0, 100.0) == 1.5     # target hit at 1.5:1
    assert db_reader.r_multiple(None, 100.0) is None
    assert db_reader.r_multiple(150.0, 0) is None


def test_entry_slippage_is_direction_aware_and_positive_means_adverse():
    """LONG filled ABOVE intent and SHORT filled BELOW intent are both adverse,
    so both must come out POSITIVE. ⛔ A raw (filled - system) would report the
    short as a gain."""
    rs, pct = db_reader.entry_slippage(100.0, 100.5, "LONG")
    assert rs == 0.5 and pct == 0.5                 # bought higher = adverse
    rs, pct = db_reader.entry_slippage(100.0, 99.5, "LONG")
    assert rs == -0.5                               # bought cheaper = favourable
    rs, _ = db_reader.entry_slippage(100.0, 99.5, "SHORT")
    assert rs == 0.5                                # sold lower = adverse
    rs, _ = db_reader.entry_slippage(100.0, 100.5, "SHORT")
    assert rs == -0.5
    assert db_reader.entry_slippage(None, 100.0, "LONG") == (None, None)


def test_result_vocabulary_never_guesses():
    """An unrecognised exit_reason resolves to the neutral 'Closed'. ⚠️ GTT_EXIT
    is a MECHANISM, not a leg, so it must NOT become SL Hit or TGT Hit."""
    r = db_reader._trade_result_of
    assert r({"status": "CLOSED", "exit_reason": "SL_HIT"}) == "SL Hit"
    assert r({"status": "CLOSED", "exit_reason": "TGT_HIT"}) == "TGT Hit"
    assert r({"status": "CLOSED_MANUAL", "exit_reason": "MANUAL"}) == "Manual Exit"
    assert r({"status": "CLOSED", "exit_reason": "GTT_EXIT"}) == "Closed"
    assert r({"status": "CLOSED", "exit_reason": "something new"}) == "Closed"
    assert r({"status": "CLOSED", "exit_reason": "EOD"}) == "Expired"
    # ⛔ The three no-exposure states are their OWN results, never 'Closed' —
    # they are two-thirds of all rows in production.
    assert r({"status": "FAILED"}) == "Failed"
    assert r({"status": "REJECTED"}) == "Rejected"
    assert r({"status": "CANCELLED"}) == "Cancelled"
    assert r({"status": "OPEN"}) == "Open"


# ─────────────────────────────────────────────────────────────────────────────
# Rows over the fixture
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def rows(gui_config):
    return trading._trade_rows_enriched(gui_config, YDAY, TODAY)


def test_rows_carry_the_range_and_the_expected_trades(rows):
    by = {r["trade_id"]: r for r in rows}
    # 4 closed + 4 open today, plus the two YDAY period trades.
    for tid in ("trd_c1", "trd_c2", "trd_c3", "trd_c4", "trd_o1", "trd_w1", "trd_w2"):
        assert tid in by, tid
    assert by["trd_c1"]["date"] == TODAY
    assert by["trd_w1"]["date"] == YDAY


def test_filled_sl_appears_only_on_an_sl_hit_and_tgt_only_on_a_tgt_hit(rows):
    """⛔⛔ THE FABRICATION GUARD. orders.avg_fill_price is NULL on every order
    ever placed, so the ONLY knowable executed price of a leg is trades.exit_price
    on a trade whose exit_reason names that leg."""
    by = {r["trade_id"]: r for r in rows}

    sl_hit = by["trd_c1"]                       # exit_reason SL_HIT
    assert sl_hit["result"] == "SL Hit"
    assert sl_hit["sl_filled"] == sl_hit["exit_price"]
    assert sl_hit["tgt_filled"] is None         # ⛔ the target did not execute

    tgt_hit = by["trd_c4"]                      # exit_reason TGT_HIT
    assert tgt_hit["result"] == "TGT Hit"
    assert tgt_hit["tgt_filled"] == tgt_hit["exit_price"]
    assert tgt_hit["sl_filled"] is None

    manual = by["trd_c3"]                       # exit_reason MANUAL
    assert manual["sl_filled"] is None and manual["tgt_filled"] is None

    an_open = by["trd_o1"]
    assert an_open["sl_filled"] is None and an_open["tgt_filled"] is None

    # And, over the whole set, the invariant holds with no exceptions.
    for r in rows:
        if r["sl_filled"] is not None:
            assert (r["exit_reason"] or "").upper() == "SL_HIT", r["trade_id"]
        if r["tgt_filled"] is not None:
            assert (r["exit_reason"] or "").upper() == "TGT_HIT", r["trade_id"]


def test_filled_is_never_copied_from_the_broker_standing_value(rows):
    """The fixture seeds an SL whose broker trigger MISMATCHES the system level.
    A reader that filled the Filled column from the broker value would put that
    trigger here; the executed price is a third, different number."""
    c1 = {r["trade_id"]: r for r in rows}["trd_c1"]
    assert c1["sl_broker"] is not None
    assert c1["sl_filled"] is not None
    assert c1["sl_filled"] != c1["sl_broker"]
    assert c1["sl_filled"] != c1["sl_initial"]


def test_rr_planned_is_per_trade_and_never_defaulted(rows):
    """⭐ Three different seeded values and one NULL. A reader returning a
    constant — which production data (every strategy at 1.5) could not expose —
    fails here."""
    by = {r["trade_id"]: r for r in rows}
    assert by["trd_c1"]["rr_applied"] == 1.5
    assert by["trd_c2"]["rr_applied"] == 2.0
    assert by["trd_c3"]["rr_applied"] == 3.0
    # ⛔ No fallback to a global or to the price-implied ratio.
    assert by["trd_c4"]["rr_applied"] is None
    assert by["trd_c4"]["expected_rr"] is not None      # the implied one DOES exist
    assert len({by[t]["rr_applied"] for t in ("trd_c1", "trd_c2", "trd_c3")}) == 3


def test_rr_planned_and_r_multiple_are_separate_fields(rows):
    """They must never collapse into one another."""
    c1 = {r["trade_id"]: r for r in rows}["trd_c1"]
    assert c1["rr_applied"] == 1.5                       # planned
    assert c1["r_multiple"] == db_reader.r_multiple(c1["net_pnl"], c1["risk_amount"])
    assert c1["r_multiple"] < 0                          # it was a loss
    assert c1["rr_applied"] != c1["r_multiple"]


def test_roi_uses_margin_reserved_on_a_real_row(rows):
    c1 = {r["trade_id"]: r for r in rows}["trd_c1"]
    assert c1["capital_committed"] == round(c1["margin_reserved"], 2)
    assert c1["roi_pct"] == db_reader.roi_pct(c1["net_pnl"], c1["margin_reserved"])
    # ⛔ NOT the notional the sizer recorded.
    assert c1["actual_position_value_rs"] not in (None, c1["margin_reserved"])
    assert c1["roi_pct"] != db_reader.roi_pct(c1["net_pnl"],
                                              c1["actual_position_value_rs"])


def test_the_two_score_quantities_are_present_and_distinct(rows):
    """CANONICAL (Rama, 13-Aug-2026): System Score = achieved, Score Threshold =
    required. ⛔ 'Signal Score' is retired."""
    c1 = {r["trade_id"]: r for r in rows}["trd_c1"]
    assert c1["system_score"] == 72                      # the ACHIEVED score
    assert c1["score_threshold"] == 65                   # the score it had to reach
    # ⭐ TWO DIFFERENT REAL NUMBERS. With the fixture seeding one value for both,
    # a reader that returned the threshold under the "System Score" label — the
    # exact defect corrected on 13-Aug — would pass.
    assert c1["system_score"] != c1["score_threshold"]
    assert "signal_score" not in c1


def test_charges_is_null_not_zero_when_nothing_was_traded(gui_config):
    """⛔ The ungated gross-minus-net fallback yields 0.00 for every trade that
    never opened exposure — and those are the majority of rows."""
    rows = db_reader.trade_explorer_rows(gui_config, YDAY, TODAY)
    for r in rows:
        if r["gross_pnl"] is None and r["net_pnl"] is None:
            assert r["charges"] is None, r["trade_id"]


def test_slippage_prefers_the_recorded_row_and_says_which_it_used(rows):
    """trd_c1 has an order_execution_log ENTRY row; the others do not. Both paths
    must be labelled, so a reader can tell a measurement from a reproduction."""
    by = {r["trade_id"]: r for r in rows}
    assert by["trd_c1"]["slippage_source"] == "recorded"
    assert by["trd_c1"]["slippage_rs"] == 1.0            # seeded in the exec log
    assert by["trd_c2"]["slippage_source"] == "derived"
    assert by["trd_c2"]["slippage_rs"] == db_reader.entry_slippage(
        by["trd_c2"]["entry_target_price"], by["trd_c2"]["entry_actual_price"],
        by["trd_c2"]["direction"])[0]


def test_recorded_slippage_joins_on_parent_trade_id_not_order_id(gui_config):
    """⚠️ MEASURED ON PRODUCTION DATA: order_execution_log.order_id holds an
    INTERNAL id (`ord_<hex>`) while orders.order_id holds the BROKER id
    (`260813170888908`). They are different id spaces and joining them returns
    ZERO rows for every trade ever placed. This pins the working key."""
    got = db_reader.trade_recorded_slippage(gui_config, ["trd_c1", "trd_c4"])
    assert "trd_c1" in got and got["trd_c1"]["slippage_rs"] == 1.0
    assert "trd_c4" not in got                            # no exec row: honest gap


def test_duration_needs_both_stamps(rows):
    by = {r["trade_id"]: r for r in rows}
    assert by["trd_c1"]["duration_sec"] > 0
    assert by["trd_o1"]["duration_sec"] is None          # still open: not measured


# ─────────────────────────────────────────────────────────────────────────────
# KPIs and summary
# ─────────────────────────────────────────────────────────────────────────────
def test_kpis_name_their_own_base(rows):
    k = db_reader.trade_explorer_kpis(rows)
    assert k["total_trades"] == len(rows)
    assert k["wins"] + k["losses"] == k["decided"]
    assert k["win_rate"] == round(k["wins"] / k["decided"] * 100.0, 2)
    # The averages are taken over the rows that HAVE the quantity, and the count
    # of those rows travels with the average.
    assert k["roi_basis"] == sum(1 for r in rows if r["roi_pct"] is not None)
    assert k["r_basis"] == sum(1 for r in rows if r["r_multiple"] is not None)
    assert k["closed_trades"] == sum(1 for r in rows if r["net_pnl"] is not None)


def test_profit_factor_is_none_rather_than_infinite(rows):
    winners = [r for r in rows if (r.get("net_pnl") or 0) > 0]
    k = db_reader.trade_explorer_kpis(winners)
    assert k["losses"] == 0
    assert k["profit_factor"] is None      # ⛔ not inf, ⛔ not a large sentinel


def test_summary_panels_are_all_real(rows):
    s = db_reader.trade_explorer_summary(rows)
    assert sum(s["outcome_mix"].values()) == s["outcome_total"] == len(rows)
    assert set(s["outcome_mix"]) <= set(db_reader.TRADE_RESULTS)
    assert s["win_loss"]["decided"] == s["win_loss"]["wins"] + s["win_loss"]["losses"]
    assert s["by_direction"]["long"]["trades"] + s["by_direction"]["short"]["trades"] \
        <= len(rows)
    assert sum(a["trades"] for a in s["by_strategy"]) == len(rows)
    assert s["coverage"]["slippage_recorded"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint + export
# ─────────────────────────────────────────────────────────────────────────────
def test_screen_renders_and_endpoint_requires_auth(client, app):
    assert client.get("/trades").status_code == 200
    assert app.test_client().get("/api/trades/screen").status_code == 401
    assert app.test_client().get("/api/export/trades").status_code == 401


def test_the_g5c_trades_endpoint_is_untouched(client):
    """ADDITIVE: Screen-07 got /api/trades/screen; the original /api/trades keeps
    its own contract."""
    d = client.get("/api/trades?period=today").get_json()
    assert d["count"] == 8 and "rows" in d and "period" in d


def test_endpoint_payload(client):
    d = client.get("/api/trades/screen?from=%s&to=%s" % (YDAY, TODAY)).get_json()
    assert d["from"] == YDAY and d["to"] == TODAY
    assert d["count"] == len(d["rows"])
    for key in ("kpis", "summary", "results", "row_cap", "unavailable"):
        assert key in d
    assert d["results"] == list(db_reader.TRADE_RESULTS)
    assert "sl_filled" in d["unavailable"]["fields"]


def test_default_range_is_a_bounded_window_not_everything(client):
    d = client.get("/api/trades/screen").get_json()
    assert d["from"] < d["to"]
    from datetime import date
    span = (date.fromisoformat(d["to"]) - date.fromisoformat(d["from"])).days
    assert span == trading._EXPLORER_DEFAULT_DAYS


def test_no_signal_score_anywhere_in_the_screen_or_its_export(client):
    """Case-insensitive, over the rendered HTML and the generated workbook —
    ⛔ not over the template source, and ⛔ not case-sensitively (the table
    headers are uppercased by CSS, so a case-sensitive check could never fail)."""
    html = client.get("/trades").get_data(as_text=True).lower()
    assert "signal score" not in html
    assert "signal_score" not in html
    assert "system score" in html and "score threshold" in html

    payload = client.get("/api/trades/screen").get_data(as_text=True).lower()
    assert "signal_score" not in payload
    assert "system_score" in payload and "score_threshold" in payload


def test_export_columns_and_filtering(client):
    """⭐ The export re-applies the SAME predicate function the table uses, so the
    sheet cannot describe a different set from the screen."""
    r = client.get("/api/export/trades?from=%s&to=%s" % (YDAY, TODAY))
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert "Signal Score" not in header
    assert "System Score" in header and "Score Threshold" in header
    assert "ROI %" in header and "R-multiple (achieved)" in header
    assert "R:R (planned)" in header
    assert "SL (Filled)" in header and "TGT (Filled)" in header
    unfiltered_rows = ws.max_row - 1

    r2 = client.get("/api/export/trades?from=%s&to=%s&result=SL+Hit" % (YDAY, TODAY))
    ws2 = load_workbook(io.BytesIO(r2.data)).active
    assert 0 < ws2.max_row - 1 < unfiltered_rows
    i_res = header.index("Result")
    assert {ws2.cell(row, i_res + 1).value for row in range(2, ws2.max_row + 1)} == {"SL Hit"}
    # ⛔ and no TGT could have filled on an SL-Hit-only sheet
    i_tgt = header.index("TGT (Filled)")
    assert all(ws2.cell(row, i_tgt + 1).value is None
               for row in range(2, ws2.max_row + 1))


def test_export_and_table_share_one_filter_implementation():
    """A test that would go red if a second copy of the predicates appeared."""
    rows = [{"strategy": "a", "symbol": "X", "trade_type": "Intraday",
             "direction": "LONG", "result": "SL Hit"},
            {"strategy": "b", "symbol": "Y", "trade_type": "Delivery",
             "direction": "SHORT", "result": "TGT Hit"}]
    f = {"strategy": "", "symbol": "", "trade_type": "",
         "direction": "SHORT", "result": ""}
    assert [r["strategy"] for r in trading._apply_trade_filters(rows, f)] == ["b"]
    f = {"strategy": "", "symbol": "", "trade_type": "",
         "direction": "", "result": "SL Hit"}
    assert [r["strategy"] for r in trading._apply_trade_filters(rows, f)] == ["a"]


# ─────────────────────────────────────────────────────────────────────────────
# Local-development direct-open (backend/app.py)
# ─────────────────────────────────────────────────────────────────────────────
def test_local_dev_bootstrap_is_off_by_default_in_the_tracked_config():
    """⛔ The committed gui_config.yaml must never ship it armed."""
    import os
    import yaml
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "backend", "config", "gui_config.yaml")
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    assert cfg["local_dev"]["auto_login"] is False


def test_local_dev_needs_all_four_gates(monkeypatch, gui_config):
    """⭐ Written so it CAN go red: the last case turns every gate on and asserts
    it DOES arm, so a guard that simply always returned False would fail."""
    from backend import app as app_module
    import copy

    cfg = copy.deepcopy(gui_config)
    cfg["local_dev"] = {"auto_login": True}
    cfg.setdefault("server", {})["bind_host"] = "127.0.0.1"

    monkeypatch.delenv("OPS_DASHBOARD_LOCAL_DEV", raising=False)
    assert app_module._local_dev_armed(cfg) is False        # no env var

    monkeypatch.setenv("OPS_DASHBOARD_LOCAL_DEV", "1")
    off = copy.deepcopy(cfg)
    off["local_dev"]["auto_login"] = False
    assert app_module._local_dev_armed(off) is False        # config off

    lan = copy.deepcopy(cfg)
    lan["server"]["bind_host"] = "0.0.0.0"
    assert app_module._local_dev_armed(lan) is False        # non-loopback bind

    assert app_module._local_dev_armed(cfg) is True         # all four → armed


def test_local_dev_refuses_a_non_loopback_client():
    from backend import app as app_module
    assert app_module._is_loopback_client("127.0.0.1") is True
    assert app_module._is_loopback_client("127.0.1.1") is True
    assert app_module._is_loopback_client("::1") is True
    assert app_module._is_loopback_client("::ffff:127.0.0.1") is True
    assert app_module._is_loopback_client("192.168.1.20") is False
    assert app_module._is_loopback_client("10.0.0.5") is False
    assert app_module._is_loopback_client("") is False
    assert app_module._is_loopback_client(None) is False


def test_login_is_still_required_when_the_bootstrap_is_not_armed(app):
    """The default posture: every screen redirects and every API 401s."""
    anon = app.test_client()
    assert anon.get("/trades").status_code == 302
    assert anon.get("/api/trades/screen").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Scanner is absent (Rama, 14-Aug-2026) — the ruling Screen-06 already carries
# ─────────────────────────────────────────────────────────────────────────────
_TPL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "frontend", "templates", "trade_explorer.html")


@pytest.fixture(scope="module")
def tpl() -> str:
    with open(_TPL, encoding="utf-8") as fh:
        return fh.read()


def _live_code(tpl: str) -> str:
    """The template with BOTH comment forms removed.

    ⚠️ Screen-06's equivalent strips only the leading `{# ... #}` design block,
    because that block legitimately NAMES the banned thing in order to record
    that it is banned. This screen records the same decision in a `/* ... */`
    block beside DEFAULT_COLS, so both forms are stripped — otherwise the test
    would fail on its own documentation while the code was correct.
    """
    out = re.sub(r"\{#.*?#\}", " ", tpl, flags=re.S)
    return re.sub(r"/\*.*?\*/", " ", out, flags=re.S)


def _default_cols(tpl: str) -> list:
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("      ],", 1)[0]
    return re.findall(r'key:\s*"([a-z_]+)"', block)


def test_scanner_has_no_column(tpl: str) -> None:
    cols = _default_cols(tpl)
    assert "scanner" not in cols
    assert "strategy" in cols               # ⛔ and the surviving one is still there
    # 27 -> 25: the SL and TGT `Broker` sub-columns were removed (Rama,
    # 21-Aug-2026). ⛔ The count is not the point of this test — the two
    # assertions above are. It is kept only so a column cannot be added back
    # silently, and it is edited only when the SET genuinely changes.
    assert len(cols) == 25


def test_scanner_appears_nowhere_in_the_live_template(tpl: str) -> None:
    """⛔ No filter, binding, heading, option list or detail row may mention it.
    Documentation may; behaviour may not."""
    assert not re.search(r"scanner", _live_code(tpl), re.IGNORECASE)


def test_scanner_is_not_an_export_column() -> None:
    labels = " ".join(lbl for lbl, _ in trading._TRADE_EXPORT_COLS).lower()
    keys = " ".join(k for _, k in trading._TRADE_EXPORT_COLS).lower()
    assert "scanner" not in labels and "scanner" not in keys


def test_scanner_is_not_a_server_side_filter(app) -> None:
    # _trade_filters reads request.args, so it needs a request context.
    with app.test_request_context("/api/trades/screen?scanner=anything"):
        f = trading._trade_filters()
    assert "scanner" not in f
    # ⭐ And the parameter is genuinely INERT, not merely unlisted: passing it
    # must not narrow the result set.
    rows = [{"strategy": "a", "symbol": "X", "trade_type": "Intraday",
             "direction": "LONG", "result": "SL Hit"}]
    assert trading._apply_trade_filters(rows, f) == rows


def test_scanner_is_not_in_the_row_payload(rows) -> None:
    """⛔ Not merely hidden from the table — not carried at all, so it cannot be
    reintroduced by a template edit alone."""
    assert all("scanner" not in r for r in rows)


def test_strategy_is_still_the_surviving_attribution(rows) -> None:
    """⭐ The removal is only safe because the two were the same value, and this
    pins that the surviving column still carries a real one."""
    assert any(r.get("strategy") for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# Column alignment (Rama, 14-Aug-2026)
# ─────────────────────────────────────────────────────────────────────────────
_CENTRED = ("time", "trade_type", "direction", "system_score",
            "score_threshold", "result")


def _col_entry(tpl: str, key: str) -> str:
    block = tpl.split("DEFAULT_COLS: [", 1)[1].split("      ],", 1)[0]
    return next(l for l in block.splitlines() if 'key: "%s"' % key in l)


def test_the_six_requested_columns_are_centred(tpl: str) -> None:
    for key in _CENTRED:
        line = _col_entry(tpl, key)
        assert "ctr: true" in line, "%s must be centred" % key


def test_the_score_columns_are_no_longer_right_aligned(tpl: str) -> None:
    """⛔ `num: true` renders `.rt`. A score is a grade, not an amount, so both
    score columns must have dropped it — ⛔ not merely gained `ctr`, which would
    leave both classes on the cell."""
    for key in ("system_score", "score_threshold"):
        assert "num: true" not in _col_entry(tpl, key), key


def test_money_columns_stay_right_aligned(tpl: str) -> None:
    """⭐ The complement, so the alignment test cannot pass by centring
    everything: real amounts must still be right-aligned for their decimals to
    line up."""
    for key in ("net_pnl", "gross_pnl", "charges", "roi_pct", "r_multiple",
                "entry_target_price", "slippage_rs",
                # `sl_broker` was removed (Rama, 21-Aug-2026). ⛔ It is not simply
                # dropped from this list — that would leave the SL/TGT band
                # untested. The four surviving money columns take its place.
                "sl_initial", "sl_filled", "tgt_initial", "tgt_filled"):
        line = _col_entry(tpl, key)
        assert "num: true" in line, key
        assert "ctr: true" not in line, key


# ─────────────────────────────────────────────────────────────────────────────
# Filter consistency (Rama, 14-Aug-2026): the deck, the panels, the chip counts
# and the table must all describe the SAME set.
# ─────────────────────────────────────────────────────────────────────────────
def _screen(client, **params):
    qs = "&".join("%s=%s" % (k, v) for k, v in params.items())
    return client.get("/api/trades/screen?" + qs).get_json()


def test_kpis_describe_the_FILTERED_set_not_the_whole_range(client):
    """⛔ THE DEFECT THIS PINS: the filters used to be applied in the browser
    only, so a filtered table sat under a deck still describing every row in the
    range. The two must move together."""
    everything = _screen(client, **{"from": YDAY, "to": TODAY})
    assert everything["count"] == everything["kpis"]["total_trades"]
    assert everything["filtered"] is False

    # ⚠️ The filter has to BITE, or the assertion below is vacuous. `direction`
    # cannot be used here: every fixture trade is LONG, so filtering on it
    # removes nothing and `filtered` is correctly False — which is how this test
    # first failed, and the fixture was right.
    one = _screen(client, **{"from": YDAY, "to": TODAY, "result": "SL+Hit"})
    assert 0 < one["count"] < everything["count"], "the filter must remove rows"
    assert one["count"] == one["kpis"]["total_trades"]
    assert one["filtered"] is True
    assert one["range_total"] == everything["count"]
    # the summary panels moved with it
    assert one["summary"]["outcome_total"] == one["count"]
    assert one["summary"]["outcome_mix"]["TGT Hit"] == 0
    assert everything["summary"]["outcome_mix"]["TGT Hit"] > 0


def test_a_filter_that_narrows_actually_narrows_every_number(client):
    """⭐ Written so it CANNOT pass vacuously: the filter must remove rows, and
    then every headline number must differ from the unfiltered one."""
    everything = _screen(client, **{"from": YDAY, "to": TODAY})
    sl_only = _screen(client, **{"from": YDAY, "to": TODAY, "result": "SL+Hit"})
    assert 0 < sl_only["count"] < everything["count"], "the filter must bite"
    assert sl_only["kpis"]["total_trades"] < everything["kpis"]["total_trades"]
    assert sl_only["summary"]["outcome_total"] < everything["summary"]["outcome_total"]
    assert {r["result"] for r in sl_only["rows"]} == {"SL Hit"}
    assert sl_only["summary"]["outcome_mix"]["SL Hit"] == sl_only["count"]


def test_result_chip_counts_ignore_the_result_filter_but_honour_the_others(client):
    """A chip answers 'how many if I pick this', given the OTHER filters.
    ⛔ Counting over the final set would read 0 on every unselected chip."""
    base = _screen(client, **{"from": YDAY, "to": TODAY})
    picked = _screen(client, **{"from": YDAY, "to": TODAY, "result": "SL+Hit"})
    # selecting a result does NOT collapse the other chips
    assert picked["result_counts"] == base["result_counts"]
    assert sum(picked["result_counts"].values()) == base["count"]
    # ...but a filter on a DIFFERENT axis does move them
    one_strategy = _screen(client, **{"from": YDAY, "to": TODAY,
                                      "strategy": "vwap_bounce_long"})
    assert 0 < one_strategy["count"] < base["count"], "the filter must bite"
    assert sum(one_strategy["result_counts"].values()) == one_strategy["count"]
    assert one_strategy["result_counts"] != base["result_counts"]


def test_dropdown_options_come_from_the_unfiltered_range(client):
    """⛔ Options derived from the filtered rows would collapse to the value
    already chosen, leaving a filter that cannot be changed."""
    base = _screen(client, **{"from": YDAY, "to": TODAY})
    narrowed = _screen(client, **{"from": YDAY, "to": TODAY,
                                  "strategy": base["options"]["strategies"][0]})
    assert narrowed["options"]["strategies"] == base["options"]["strategies"]
    assert len(base["options"]["strategies"]) > 1        # non-vacuous


def test_export_and_screen_return_the_same_filtered_count(client):
    """One filter implementation, so the sheet and the screen cannot disagree."""
    params = {"from": YDAY, "to": TODAY, "result": "SL+Hit"}
    screen = _screen(client, **params)
    assert screen["filtered"] is True, "a non-biting filter proves nothing here"
    r = client.get("/api/export/trades?" + "&".join("%s=%s" % kv for kv in params.items()))
    ws = load_workbook(io.BytesIO(r.data)).active
    assert ws.max_row - 1 == screen["count"]


def test_the_screen_states_the_base_its_numbers_describe(tpl: str):
    """The scope line is not decoration — without it a filtered deck and a
    filtered table look identical to an unfiltered pair."""
    assert "scopeLine()" in tpl
    assert "range_total" in tpl and "d.filtered" in tpl


def test_no_currency_symbol_in_any_cell_renderer(tpl: str) -> None:
    """Amounts carry no rupee sign in CELLS; the headings do.

    ⚠️ Comments are stripped first — `cell()` carries the note "no rupee in
    cells", which contains the symbol it is forbidding. Scanning raw source
    would fail on the documentation while the code was right.
    """
    block = tpl.split("cell(r, c) {", 1)[1].split("      resultPill(", 1)[0]
    assert "₹" not in re.sub(r"/\*.*?\*/", " ", block, flags=re.S)
    cols = tpl.split("DEFAULT_COLS: [", 1)[1].split("      ],", 1)[0]
    assert "₹" in cols            # ...but the headings do carry it
