"""
tests/unit/test_reconciler_product_filter.py — ledger #2b (Q4 buy-day filter,
the reconciler's sell-under-kill site).

The site: `order_reconciler._check2_inflight_orphan` flattens an in-flight-entry
orphan WHILE HARD_KILL IS ACTIVE (`_flatten_broker_position`). It was the third
sell-under-kill path — disclosed by the ledger-#2 build (§2), product-blind, and
patched here with the SAME cure and the SAME single vocabulary as the two
kill_switch sites: CNC survives (Q4: "no live INTRADAY position"), NULL/unknown
flattens LOUDLY (G2).

Mirrors tests/unit/test_kill_switch_product_filter.py for this site, plus the
two properties that are specific to it:
  * a spared CNC orphan must stay VISIBLE to the next cycle (no suppression),
  * `_flatten_broker_position` must stay single-caller, because that is what
    makes a filter in the caller equivalent to a filter in the sell itself.
"""

import inspect
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import orders.order_reconciler as recon_mod
from core.constants import EMERGENCY_FLATTEN_PRODUCTS


@dataclass
class _BP:
    """A broker position row as the adapter yields it — Position(symbol, qty,
    avg_price, product, side); `product` is Kite's RAW string (G3)."""
    symbol: str
    qty: int
    avg_price: float
    product: str


def _recon(tmp_path, *, kill_active=True, quote_ltp=200.0):
    from tests.unit.test_order_reconciler import _make_reconciler, _make_store

    store = _make_store(tmp_path)
    ks = MagicMock()
    ks.is_active.return_value = kill_active
    adapter = MagicMock()
    adapter.place_order.return_value = type("P", (), {"broker_order_id": "BX"})()
    quote_fn = lambda syms: {  # noqa: E731 — bare-symbol keying (M-O1)
        s: type("Q", (), {"last_price": quote_ltp})() for s in syms
    }
    return _make_reconciler(
        store, adapter=adapter, kill_switch=ks, quote_fn=quote_fn
    ), store, adapter, ks


def _trade(tid="t1", status="PENDING_FILL"):
    return {"trade_id": tid, "status": status}


# ── the filter: MIS/CO flatten, CNC survives, unknown flattens loudly ────────

# ⛔ SUPERSEDED-BUT-LEGIBLE (#2c-R, 02-Aug). This case originally ran over the
# WHOLE shared set — sorted(EMERGENCY_FLATTEN_PRODUCTS) == ["CO", "MIS"] — and
# asserted that BOTH flatten quietly. CO's row is now WRONG, not weakened: #2c
# Step-1 measured that a CO position cannot be squared by a reverse order at all
# (Audit 3.1), so this site REFUSES it. The original concern — "a KNOWN intraday
# product flattens, and does so QUIETLY (no unknown-product alert)" — survives
# in full for every product this site still sells, and CO's own behaviour is
# pinned by tests/unit/test_reconciler_co_refusal.py.
# ⛔ Still DERIVED from the shared constant, never a hardcoded list: a product
# added to EMERGENCY_FLATTEN_PRODUCTS is automatically covered here, and the
# subtraction below is itself asserted so the exclusion cannot silently widen.
_REFUSED_AT_THIS_SITE = frozenset({"CO"})   # #2c-R, site-local (the set is shared)


def test_the_refusal_exclusion_is_exactly_co_and_the_set_is_unnarrowed():
    # If either half of this drifts, the parametrisation below is lying.
    assert _REFUSED_AT_THIS_SITE == frozenset({"CO"})
    assert _REFUSED_AT_THIS_SITE < EMERGENCY_FLATTEN_PRODUCTS   # proper subset
    assert EMERGENCY_FLATTEN_PRODUCTS - _REFUSED_AT_THIS_SITE == frozenset({"MIS"})


@pytest.mark.parametrize(
    "product", sorted(EMERGENCY_FLATTEN_PRODUCTS - _REFUSED_AT_THIS_SITE)
)
def test_intraday_products_flatten_quietly(tmp_path, product):
    recon, store, adapter, ks = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "GICRE", _BP("GICRE", 4, 199.0, product), _trade()
    )
    assert action.check_name == "INFLIGHT_ORPHAN_FLATTEN"
    kw = adapter.place_order.call_args.kwargs
    assert (kw["symbol"], kw["side"], kw["qty"]) == ("GICRE", "SELL", 4)
    assert kw["order_type"] == "LIMIT" and kw["price"] == pytest.approx(198.0)
    ks._alert_unknown_product.assert_not_called()  # known intraday: quiet
    store.close()


def test_cnc_is_spared_and_nothing_is_sold(tmp_path):
    recon, store, adapter, ks = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "DELIVCO", _BP("DELIVCO", 7, 199.0, "CNC"), _trade()
    )
    adapter.place_order.assert_not_called()      # delivery survives the kill
    assert action.check_name == "INFLIGHT_ORPHAN_SPARED_DELIVERY"
    assert action.tier == "CRITICAL" and action.success is True
    assert "SPARED" in action.description
    ks._alert_unknown_product.assert_not_called()  # sparing CNC is by design
    store.close()


def test_short_cnc_row_is_spared_too(tmp_path):
    # The spare is on PRODUCT, never on direction: a negative-qty CNC row must
    # not slip through into a BUY-to-flatten.
    recon, store, adapter, _ks = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "DELIVCO", _BP("DELIVCO", -7, 199.0, "CNC"), _trade()
    )
    adapter.place_order.assert_not_called()
    assert action.check_name == "INFLIGHT_ORPHAN_SPARED_DELIVERY"
    store.close()


@pytest.mark.parametrize("product", ["", "NRML", "MTF", "cnc_typo"])
def test_unknown_product_flattens_with_the_shared_critical_emitter(
    tmp_path, product
):
    recon, store, adapter, ks = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "MYSTERY", _BP("MYSTERY", 3, 199.0, product), _trade()
    )
    # G2: included in the flatten…
    assert action.check_name == "INFLIGHT_ORPHAN_FLATTEN"
    adapter.place_order.assert_called_once()
    # …and LOUD, through the ONE shared emitter (not a second definition).
    ks._alert_unknown_product.assert_called_once()
    kw = ks._alert_unknown_product.call_args.kwargs
    assert kw["site"] == "reconciler_check2"
    assert kw["symbol"] == "MYSTERY" and kw["qty"] == 3
    assert kw["raw_product"] == product.strip().upper()
    store.close()


def test_lowercase_cnc_is_still_spared(tmp_path):
    # The predicate normalises like both kill_switch sites do; a case variant
    # must not fall through to the flatten.
    recon, store, adapter, _ks = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "DELIVCO", _BP("DELIVCO", 7, 199.0, " cnc "), _trade()
    )
    adapter.place_order.assert_not_called()
    assert action.check_name == "INFLIGHT_ORPHAN_SPARED_DELIVERY"
    store.close()


def test_a_broken_emitter_still_leaves_the_flatten_loud(tmp_path, caplog):
    # Alerting must never break the flatten, and must never make it quiet.
    recon, store, adapter, ks = _recon(tmp_path)
    ks._alert_unknown_product.side_effect = RuntimeError("notifier gone")
    with caplog.at_level("CRITICAL", logger="order_reconciler"):
        action = recon._check2_inflight_orphan(
            "MYSTERY", _BP("MYSTERY", 3, 199.0, ""), _trade()
        )
    assert action.check_name == "INFLIGHT_ORPHAN_FLATTEN"
    adapter.place_order.assert_called_once()
    assert any("UNKNOWN PRODUCT" in r.message for r in caplog.records)
    store.close()


# ── the kill gate is unchanged: no kill ⇒ no product decision at all ─────────

def test_no_kill_no_action_for_any_product(tmp_path):
    for product in ("MIS", "CNC", ""):
        recon, store, adapter, ks = _recon(tmp_path, kill_active=False)
        action = recon._check2_inflight_orphan(
            "GICRE", _BP("GICRE", 4, 199.0, product), _trade()
        )
        assert action.check_name == "INFLIGHT_ORPHAN"
        assert action.tier == "COSMETIC"
        adapter.place_order.assert_not_called()
        ks._alert_unknown_product.assert_not_called()
        store.close()


# ── the site-specific hazard: a spare must not resolve the orphan ────────────

def test_spared_cnc_stays_visible_to_the_next_cycle(tmp_path):
    """A spared CNC orphan is STILL an orphan. Nothing may mark it handled or
    resolved: the identical call on the next cycle must produce the identical
    CRITICAL disposition (re-alerting each cycle is the contract, not a bug)."""
    recon, store, adapter, _ks = _recon(tmp_path)
    bp, trade = _BP("DELIVCO", 7, 199.0, "CNC"), _trade()
    first = recon._check2_inflight_orphan("DELIVCO", bp, trade)
    second = recon._check2_inflight_orphan("DELIVCO", bp, trade)
    third = recon._check2_inflight_orphan("DELIVCO", bp, trade)
    for act in (first, second, third):
        assert act.check_name == "INFLIGHT_ORPHAN_SPARED_DELIVERY"
        assert act.tier == "CRITICAL"      # non-COSMETIC ⇒ persisted each cycle
    adapter.place_order.assert_not_called()
    store.close()


def test_sparing_one_symbol_does_not_silence_another(tmp_path):
    # No per-symbol suppression may leak across symbols either: a spared CNC on
    # one symbol must not stop an MIS orphan on another from being flattened.
    recon, store, adapter, _ks = _recon(tmp_path)
    recon._check2_inflight_orphan(
        "DELIVCO", _BP("DELIVCO", 7, 199.0, "CNC"), _trade("t1")
    )
    action = recon._check2_inflight_orphan(
        "GICRE", _BP("GICRE", 4, 199.0, "MIS"), _trade("t2")
    )
    assert action.check_name == "INFLIGHT_ORPHAN_FLATTEN"
    assert adapter.place_order.call_args.kwargs["symbol"] == "GICRE"
    store.close()


def test_a_spare_is_not_an_orphan_recovery_for_the_closure_classifier(tmp_path):
    """The spare's check_name must NOT enter reports' ORPHAN_RECOVERY set — a
    spare closes nothing, and classifying it as a recovery would report a live
    delivery position as a recovered/closed one."""
    from reports.daily_trade_review import _ORPHAN_CHECKS

    recon, store, _adapter, _ks = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "DELIVCO", _BP("DELIVCO", 7, 199.0, "CNC"), _trade()
    )
    assert action.check_name not in _ORPHAN_CHECKS
    store.close()


# ── the vocabulary + the single-caller premise ───────────────────────────────

def test_reconciler_reads_the_one_shared_vocabulary():
    src = inspect.getsource(recon_mod)
    assert "EMERGENCY_FLATTEN_PRODUCTS" in src
    # …and no inline restatement of the flatten vocabulary (the #2 scanner
    # idiom: re-inlining the set anywhere in this module trips this test).
    assert '("MIS", "CO")' not in src, "inline vocabulary appeared in reconciler"
    assert '{"MIS", "CO"}' not in src, "inline vocabulary appeared in reconciler"


def test_flatten_broker_position_has_exactly_one_caller():
    """The filter sits in the CALLER. That is only equivalent to filtering the
    sell itself while `_flatten_broker_position` has exactly one caller — the
    filtered one. A second caller would be an unfiltered sell-under-kill path,
    which is the whole defect #2b closes."""
    src = inspect.getsource(recon_mod)
    callers = [
        ln.strip() for ln in src.splitlines()
        if "_flatten_broker_position(" in ln
        and not ln.strip().startswith(("#", "*", "def ", "self, symbol"))
        and "def _flatten_broker_position" not in ln
    ]
    assert callers == ["ok = self._flatten_broker_position(symbol, bp, \"KILL\", trade_id)"], (
        f"_flatten_broker_position gained/changed a caller: {callers} — a new "
        f"caller must apply the EMERGENCY_FLATTEN_PRODUCTS filter (Q4/#2b)"
    )
