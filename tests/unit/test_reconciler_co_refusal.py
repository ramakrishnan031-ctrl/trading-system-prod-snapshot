"""
tests/unit/test_reconciler_co_refusal.py — ledger #2c-R (the approved redesign).

`order_reconciler._check2_inflight_orphan` must REFUSE to flatten a COVER ORDER
(CO) position under an active HARD_KILL, and escalate instead of selling.

WHY (#2c Step-1, measured): a CO position cannot be squared off by a reverse
order at all — the broker rejects it and auto-squares at 15:20 with a penalty
(Audit 3.1); the correct path is cancel_order(entry_broker_id, variety="co") on
the parent bracket, and this path has no parent order id. The old behaviour sold
product="MIS", which the broker ACCEPTS but which does not net against a CO
position (Kite nets per (symbol, product)) — leaving a NAKED MIS SHORT.

⚠️ PARITY IS PROVEN HERE, NOT BY A PAPER DRILL. Paper nets by SYMBOL while live
Kite nets per (symbol, product), so a paper drill of this class is vacuously
green (#2c Step-1 finding (f), now a standing rule).

⛔ CO is a MEMBER of the shared EMERGENCY_FLATTEN_PRODUCTS frozenset, read by
five sites. The refusal is therefore a reconciler-site-LOCAL branch placed BEFORE
the membership test — a test below pins that the shared constant is unnarrowed.
"""

import inspect
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import orders.order_reconciler as recon_mod
from core.constants import EMERGENCY_FLATTEN_PRODUCTS


@dataclass
class _BP:
    """A broker position row as the adapter yields it; `product` is Kite's RAW
    string (G3) — validated independently of the local vocabulary."""
    symbol: str
    qty: int
    avg_price: float
    product: str


def _recon(tmp_path, *, kill_active=True):
    from tests.unit.test_order_reconciler import _make_reconciler, _make_store

    store = _make_store(tmp_path)
    ks = MagicMock()
    ks.is_active.return_value = kill_active
    adapter = MagicMock()
    adapter.place_order.return_value = type("P", (), {"broker_order_id": "BX"})()
    notifier = MagicMock()
    quote_fn = lambda syms: {  # noqa: E731
        s: type("Q", (), {"last_price": 200.0})() for s in syms
    }
    recon = _make_reconciler(
        store, adapter=adapter, kill_switch=ks, notifier=notifier, quote_fn=quote_fn
    )
    return recon, store, adapter, notifier


def _trade(tid="t1", status="PENDING_FILL"):
    return {"trade_id": tid, "status": status}


# ── the refusal ──────────────────────────────────────────────────────────────

def test_co_position_is_refused_and_nothing_is_sold(tmp_path):
    recon, store, adapter, notifier = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "COSYM", _BP("COSYM", 4, 199.0, "CO"), _trade()
    )
    # THE POINT: no order of any kind. Placing a wrong, position-CREATING order
    # is the defect; refusing leaves the book as-is.
    adapter.place_order.assert_not_called()
    assert action.check_name == "INFLIGHT_ORPHAN_REFUSED_CO"
    assert action.tier == "CRITICAL"
    # correct-but-INCOMPLETE: the position is still live and still needs a human,
    # so the audit row must NOT claim success (contrast the CNC spare).
    assert action.success is False
    assert "REFUS" in action.action_taken.upper()
    store.close()


def test_co_refusal_escalates_with_a_critical_naming_the_reason(tmp_path, caplog):
    recon, store, _adapter, notifier = _recon(tmp_path)
    with caplog.at_level("CRITICAL", logger="order_reconciler"):
        recon._check2_inflight_orphan(
            "COSYM", _BP("COSYM", 4, 199.0, "CO"), _trade()
        )
    assert notifier.send.call_count == 1
    kw = notifier.send.call_args.kwargs
    assert kw["severity"] == "CRITICAL"
    assert "COSYM" in kw["title"]
    assert kw["source_module"] == "order_reconciler"
    body = kw["body"]
    # the reason must be named, not implied: what, why, and who acts.
    for token in ("REFUSING", "Audit 3.1", "variety", "NO ORDER WAS PLACED",
                  "OPERATOR", "reconciler_check2"):
        assert token in body, f"CRITICAL body does not name {token!r}"
    assert any("REFUSING to flatten" in r.message for r in caplog.records)
    store.close()


def test_a_broken_notifier_does_not_break_the_refusal(tmp_path):
    # Alerting must never turn a refusal into a sell, and must never raise.
    recon, store, adapter, notifier = _recon(tmp_path)
    notifier.send.side_effect = RuntimeError("telegram down")
    action = recon._check2_inflight_orphan(
        "COSYM", _BP("COSYM", 4, 199.0, "CO"), _trade()
    )
    assert action.check_name == "INFLIGHT_ORPHAN_REFUSED_CO"
    adapter.place_order.assert_not_called()
    store.close()


def test_short_co_row_is_refused_too(tmp_path):
    # The refusal is on PRODUCT, never on direction — a negative-qty CO row must
    # not slip through into a BUY-to-flatten.
    recon, store, adapter, _n = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "COSYM", _BP("COSYM", -4, 199.0, "CO"), _trade()
    )
    adapter.place_order.assert_not_called()
    assert action.check_name == "INFLIGHT_ORPHAN_REFUSED_CO"
    store.close()


@pytest.mark.parametrize("variant", [" co ", "co", "Co", "  CO"])
def test_case_and_whitespace_variants_are_still_refused(tmp_path, variant):
    recon, store, adapter, _n = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "COSYM", _BP("COSYM", 4, 199.0, variant), _trade()
    )
    adapter.place_order.assert_not_called()
    assert action.check_name == "INFLIGHT_ORPHAN_REFUSED_CO"
    store.close()


# ── the gate and the neighbours are untouched ────────────────────────────────

def test_co_under_no_kill_makes_no_product_decision_at_all(tmp_path):
    recon, store, adapter, notifier = _recon(tmp_path, kill_active=False)
    action = recon._check2_inflight_orphan(
        "COSYM", _BP("COSYM", 4, 199.0, "CO"), _trade()
    )
    assert action.check_name == "INFLIGHT_ORPHAN"
    assert action.tier == "COSMETIC"
    adapter.place_order.assert_not_called()
    notifier.send.assert_not_called()
    store.close()


def test_mis_still_flattens_and_cnc_still_spares(tmp_path):
    """The new branch must not shadow its neighbours (#2b behaviour intact)."""
    recon, store, adapter, _n = _recon(tmp_path)
    mis = recon._check2_inflight_orphan(
        "MISSYM", _BP("MISSYM", 4, 199.0, "MIS"), _trade("t1")
    )
    assert mis.check_name == "INFLIGHT_ORPHAN_FLATTEN"
    assert adapter.place_order.call_args.kwargs["symbol"] == "MISSYM"
    cnc = recon._check2_inflight_orphan(
        "CNCSYM", _BP("CNCSYM", 4, 199.0, "CNC"), _trade("t2")
    )
    assert cnc.check_name == "INFLIGHT_ORPHAN_SPARED_DELIVERY"
    assert adapter.place_order.call_count == 1  # only the MIS one ever sold
    store.close()


def test_a_co_refusal_on_one_symbol_does_not_silence_another(tmp_path):
    recon, store, adapter, _n = _recon(tmp_path)
    recon._check2_inflight_orphan("COSYM", _BP("COSYM", 4, 199.0, "CO"), _trade("t1"))
    action = recon._check2_inflight_orphan(
        "GICRE", _BP("GICRE", 4, 199.0, "MIS"), _trade("t2")
    )
    assert action.check_name == "INFLIGHT_ORPHAN_FLATTEN"
    assert adapter.place_order.call_args.kwargs["symbol"] == "GICRE"
    store.close()


def test_refused_co_stays_visible_to_the_next_cycle(tmp_path):
    """A refused CO orphan is STILL an orphan and STILL unresolved. Nothing may
    mark it handled: identical calls must produce identical CRITICAL refusals,
    cycle after cycle, until a human resolves it."""
    recon, store, adapter, notifier = _recon(tmp_path)
    bp, trade = _BP("COSYM", 4, 199.0, "CO"), _trade()
    acts = [recon._check2_inflight_orphan("COSYM", bp, trade) for _ in range(3)]
    for a in acts:
        assert a.check_name == "INFLIGHT_ORPHAN_REFUSED_CO"
        assert a.tier == "CRITICAL" and a.success is False
    assert notifier.send.call_count == 3   # deliberately NOT deduped
    adapter.place_order.assert_not_called()
    store.close()


# ── downstream + the shared-constant guard ───────────────────────────────────

def test_a_refusal_is_not_an_orphan_recovery_for_the_closure_classifier(tmp_path):
    """A refusal closes nothing. If its name entered _ORPHAN_CHECKS, reports
    would label a still-live CO position as a recovered/closed one."""
    from reports.daily_trade_review import _ORPHAN_CHECKS

    recon, store, _a, _n = _recon(tmp_path)
    action = recon._check2_inflight_orphan(
        "COSYM", _BP("COSYM", 4, 199.0, "CO"), _trade()
    )
    assert action.check_name not in _ORPHAN_CHECKS
    # …but it IS in the ORPHAN family for the two substring counters, which is
    # correct: a refused CO orphan is a real orphan detection and should warn.
    assert "ORPHAN" in action.check_name.upper()
    store.close()


def test_the_shared_flatten_vocabulary_was_not_narrowed():
    """⛔ THE BLAST-RADIUS GUARD. CO must REMAIN in the shared constant — it is
    read by five sites (kill_switch ×2, the two scheduled EOD passes, and the
    reconciler). Removing CO to implement this refusal would silently change the
    other four. The refusal is site-local instead, and this test is what makes
    that non-negotiable."""
    assert EMERGENCY_FLATTEN_PRODUCTS == frozenset({"MIS", "CO"})
    assert "CO" in EMERGENCY_FLATTEN_PRODUCTS


def test_co_refusal_precedes_the_membership_test():
    """Structural pin: because CO IS a member of the shared set, the refusal only
    works if its branch sits BEFORE the membership test. If someone reorders
    them, CO silently falls through into the flatten again."""
    src = inspect.getsource(recon_mod.OrderReconciler._check2_inflight_orphan)
    co_at = src.index('raw_product == "CO"')
    member_at = src.index("not in _EMERGENCY_FLATTEN_PRODUCTS")
    assert co_at < member_at, (
        "the CO refusal must precede the EMERGENCY_FLATTEN_PRODUCTS membership "
        "test — CO is a MEMBER of that set, so after it CO falls into the flatten"
    )
