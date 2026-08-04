"""
tests/unit/test_kill_switch_product_filter.py — ledger #2 (Q4 buy-day filter).

The HARD_KILL emergency flatten must sell INTRADAY products only: CNC survives
(Q4: "no live INTRADAY position"), NULL/unknown flattens LOUDLY (G2), and the
per-product-row hazard is covered: sparing a CNC row must never suppress the
flattening of a same-symbol MIS row (Kite positions() is per (symbol, product)).

These are behaviour tests of the filter predicate at both sites — direct calls
to _exit_all_trades_indestructible with the broker seams mocked.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import capital.kill_switch as ks_mod
from capital.kill_switch import KillSwitch
from core.constants import EMERGENCY_FLATTEN_PRODUCTS


def _mk_ks(monkeypatch, *, trades, positions, notifier=None):
    store = MagicMock()
    store.fetch_all.return_value = trades
    adapter = MagicMock()
    adapter.get_positions.return_value = positions
    adapter.place_order.return_value = SimpleNamespace(broker_order_id="B1")
    log = MagicMock()
    ks = KillSwitch(
        state_store=store,
        bus=MagicMock(),
        logger=log,
        adapter=adapter,
        notifier=notifier,
        mode="PAPER",
    )
    # Broker seams: reverse-aware close resolves to the intended side/qty;
    # marketable params and DB touch-points are not under test here.
    monkeypatch.setattr(ks_mod, "determine_close_direction",
                        lambda _a, _s, side, qty: (side, qty))
    monkeypatch.setattr(ks, "_marketable_exit_params",
                        lambda _s, _side: ("LIMIT", 100.0))
    monkeypatch.setattr(ks, "_cancel_trade_resting_exits", lambda _tid: None)
    monkeypatch.setattr(ks, "_mark_trade_exiting", lambda _tid: None)
    return ks, adapter, log


def _trade(tid, sym, product, qty=5, direction="LONG"):
    return {"trade_id": tid, "symbol": sym, "qty_filled": qty,
            "direction": direction, "product": product}


def _pos(sym, qty, product):
    return SimpleNamespace(symbol=sym, qty=qty, product=product)


def _placed_symbols(adapter):
    return [(c.kwargs["symbol"], c.kwargs["intent"], c.kwargs["tag"])
            for c in adapter.place_order.call_args_list]


# ── SITE 1 (local pass) ──────────────────────────────────────────────────────

def test_site1_mis_flattens_quietly(monkeypatch):
    """⚠️ CONTRACT NARROWED 04-Aug-2026 by ledger #2d — recorded, not rewritten.

    This test used to be `test_site1_mis_and_co_flatten_quietly` and asserted
    that BOTH a MIS and a CO trade received a reverse order ("AAA", "BBB").
    That was ledger #2's view, where CO was simply another intraday product to
    flatten. It is now known to be WRONG for CO: Audit 3.1 — Zerodha REJECTS a
    reverse order on a CO position and auto-squares it at 15:20 with a Rs50+GST
    penalty. A CO position is closed by cancelling its bracket, never by a
    reverse.

    The MIS half was always right and is unchanged here. The CO half moved to
    tests/unit/test_ledger2d_co_bracket.py, which drives the real bracket-cancel
    path against a real StateStore; the guard below only pins that CO no longer
    takes THIS path.
    """
    notifier = MagicMock()
    ks, adapter, _ = _mk_ks(monkeypatch,
                            trades=[_trade("t1", "AAA", "MIS")],
                            positions=[], notifier=notifier)
    report = ks._exit_all_trades_indestructible()
    assert [s for s, _, _ in _placed_symbols(adapter)] == ["AAA"]
    notifier.send.assert_not_called()  # a known intraday product: quiet
    assert report.attempted == 1 and report.succeeded == 1


def test_site1_co_is_never_given_a_reverse_order(monkeypatch):
    """Ledger #2d guard at this site: whatever else happens to a CO trade, it
    must NOT receive a place_order. Here the bracket lookup is made to fail
    deterministically (no ENTRY row), so the trade takes the R-a refusal path --
    and a refusal must still never fall through to a reverse."""
    notifier = MagicMock()
    ks, adapter, log = _mk_ks(monkeypatch,
                              trades=[_trade("t2", "BBB", "CO")],
                              positions=[], notifier=notifier)
    ks._store.fetch_one.return_value = None   # no CO entry row -> cannot confirm
    report = ks._exit_all_trades_indestructible()

    assert _placed_symbols(adapter) == [], (
        "Audit 3.1: a CO position must never receive a reverse order"
    )
    assert any("KS_CO_VARIETY_DIVERGENCE" in str(c)
               for c in log.critical.call_args_list), "the refusal must be loud"
    assert report.failed == ["t2"] and report.succeeded == 0, (
        "a refused CO position may still be live -- it is not a success"
    )


def test_site1_cnc_spared_and_attempt_count_honest(monkeypatch):
    notifier = MagicMock()
    ks, adapter, log = _mk_ks(monkeypatch,
                              trades=[_trade("t1", "DDD", "CNC"),
                                      _trade("t2", "AAA", "MIS")],
                              positions=[], notifier=notifier)
    report = ks._exit_all_trades_indestructible()
    assert [s for s, _, _ in _placed_symbols(adapter)] == ["AAA"]
    # the spared CNC is logged loudly and NOT counted as attempted/flattened
    assert any("SPARED delivery position" in str(c)
               for c in log.critical.call_args_list)
    assert report.attempted == 1 and report.succeeded == 1
    notifier.send.assert_not_called()  # sparing CNC is by design, not an alarm


def test_site1_null_product_flattens_with_critical(monkeypatch):
    notifier = MagicMock()
    ks, adapter, _ = _mk_ks(monkeypatch,
                            trades=[_trade("t1", "EEE", None)],
                            positions=[], notifier=notifier)
    ks._exit_all_trades_indestructible()
    placed = _placed_symbols(adapter)
    assert placed == [("EEE", "INTRADAY", "ks_hard_kill_exit")]
    assert notifier.send.call_count == 1
    assert notifier.send.call_args.kwargs["severity"] == "CRITICAL"
    assert "UNKNOWN PRODUCT" in notifier.send.call_args.kwargs["title"]


def test_site1_nrml_flattens_loud_under_its_own_intent(monkeypatch):
    # 2e ruling: NRML should not exist on this account -> anomaly: flatten +
    # CRITICAL; the exit still uses NRML's own intent (H-5 correctness).
    notifier = MagicMock()
    ks, adapter, _ = _mk_ks(monkeypatch,
                            trades=[_trade("t1", "FFF", "NRML")],
                            positions=[], notifier=notifier)
    ks._exit_all_trades_indestructible()
    assert _placed_symbols(adapter) == [("FFF", "DELIVERY", "ks_hard_kill_exit")]
    assert notifier.send.call_count == 1


# ── SITE 2 (broker sweep) ────────────────────────────────────────────────────

def test_site2_cnc_excluded_unknown_included_loud(monkeypatch):
    notifier = MagicMock()
    ks, adapter, log = _mk_ks(monkeypatch, trades=[],
                              positions=[_pos("GGG", 3, "CNC"),
                                         _pos("HHH", 2, "")],
                              notifier=notifier)
    ks._exit_all_trades_indestructible()
    assert _placed_symbols(adapter) == [("HHH", "INTRADAY", "ks_hard_kill_sweep")]
    assert any("SPARED delivery position" in str(c)
               for c in log.critical.call_args_list)
    assert notifier.send.call_count == 1  # the unknown "" product, loud


def test_site2_spared_cnc_does_not_suppress_same_symbol_mis_row(monkeypatch):
    # THE per-product-row hazard: SYM holds a CNC row AND an MIS row at the
    # broker. The CNC row is spared; the MIS row MUST still be flattened.
    ks, adapter, _ = _mk_ks(monkeypatch, trades=[],
                            positions=[_pos("SYM", 3, "CNC"),
                                       _pos("SYM", 4, "MIS")])
    ks._exit_all_trades_indestructible()
    assert _placed_symbols(adapter) == [("SYM", "INTRADAY", "ks_hard_kill_sweep")]
    assert adapter.place_order.call_args.kwargs["qty"] == 4


def test_site1_spared_cnc_does_not_suppress_broker_mis_sweep(monkeypatch):
    # Same hazard across sites: a LOCAL CNC trade on SYM is spared in the
    # local pass; the broker also reports an MIS row on SYM — the sweep must
    # still flatten it (the spared symbol is deliberately NOT marked handled).
    ks, adapter, _ = _mk_ks(monkeypatch,
                            trades=[_trade("t1", "SYM", "CNC")],
                            positions=[_pos("SYM", 4, "MIS"),
                                       _pos("SYM", 3, "CNC")])
    ks._exit_all_trades_indestructible()
    tags = [t for _, _, t in _placed_symbols(adapter)]
    assert tags == ["ks_hard_kill_sweep"]
    assert adapter.place_order.call_args.kwargs["symbol"] == "SYM"
    assert adapter.place_order.call_args.kwargs["qty"] == 4


# ── the vocabulary: one source, no drift ─────────────────────────────────────

def test_vocabulary_single_source_and_membership():
    assert EMERGENCY_FLATTEN_PRODUCTS == frozenset({"MIS", "CO"})
    import inspect
    import orders.eod_squareoff as eod_mod
    ks_src = inspect.getsource(ks_mod)
    eod_src = inspect.getsource(eod_mod)
    # both consumers reference the ONE name…
    assert "EMERGENCY_FLATTEN_PRODUCTS" in ks_src
    assert "EMERGENCY_FLATTEN_PRODUCTS" in eod_src
    # …and no inline restatement of the flatten vocabulary survives in either
    # (the scanner idiom: re-inlining the set anywhere here trips this test).
    for src, name in ((ks_src, "kill_switch"), (eod_src, "eod_squareoff")):
        assert '("MIS", "CO")' not in src, f"inline vocabulary reappeared in {name}"
        assert '{"MIS", "CO"}' not in src, f"inline vocabulary reappeared in {name}"
