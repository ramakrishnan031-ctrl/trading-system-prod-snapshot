"""
tests/unit/test_mis_squareoff_restore_and_settle.py — F1 + F2, 03-Sep-2026.

THE DEFECT, MEASURED TWICE
--------------------------
`mis_autosquareoff` cancels the protective orders FIRST and only then decides
whether it can exit. Before F1 there was no undo: every failure after the cancel
returned with the stop gone and no sell placed.

  02-Sep COALINDIA  PASS_1 CANCEL_FAILED 15:07:02 -> naked -> G5b recovered it
                    13 s later -> closed externally -> no incident.
  03-Sep ANANTRAJ   PASS_1 CANCEL_FAILED 15:07:04 -> naked -> G5b recovered ->
                    survived to PASS_2 15:10:03, which cancelled the RECOVERY SL
                    -> G5b then blocked by a stale local row -> CHECK9 ->
                    8 rejected MARKET exits -> flattened by hand at 15:12:11.

F2 addresses the cause of the CANCEL_FAILED: the verification polled ONCE at
+27-53 ms. The same orders read terminal at +1.115 s. 3 of 3 failed that way.
F1 addresses the consequence: whatever the reason, the position must not be left
without a stop.

(docs/incident/2026-09-03_naked_position_ANANTRAJ.md)
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import orders.mis_autosquareoff as mod
from orders.mis_autosquareoff import MisAutoSquareoff

_LOG = logging.getLogger("test_mis_restore")

_TRADE = "trd_b7d0f9ce3b31"
_SYM = "ANANTRAJ"


def _hist(status: str):
    """One order-history entry with the given status."""
    return [SimpleNamespace(status=status)]


def _resting_sl():
    """A resting row as get_open_mis_exit_orders_for_symbol returns it:
    trade_id, symbol, leg, variety, order_id -- and NOTHING about price/qty."""
    return [{"trade_id": _TRADE, "symbol": _SYM, "leg": "SL",
             "variety": "regular", "order_id": "260903170310432"}]


def _sl_order_row():
    """The local orders row the restore must read its parameters back from."""
    return {
        "order_id": "260903170310432", "trade_id": _TRADE, "leg": "SL",
        "transaction_type": "SELL", "order_type": "SL", "product": "MIS",
        "variety": "regular", "qty_requested": 1,
        "price": 616.95, "trigger_price": 620.07705225,
    }


def _bare(adapter=None, store=None) -> MisAutoSquareoff:
    """A MisAutoSquareoff with only the collaborators these two units touch.
    __new__ deliberately: the real constructor wires a poll thread and a timing
    contract, neither of which is under test here."""
    obj = MisAutoSquareoff.__new__(MisAutoSquareoff)
    obj._adapter = adapter if adapter is not None else MagicMock()
    obj._store = store if store is not None else MagicMock()
    obj._log = _LOG
    return obj


# ── F2 · the settle window ───────────────────────────────────────────────────

def test_f2_a_late_but_terminal_cancel_is_success(monkeypatch) -> None:
    """RED before F2. The measured shape: non-terminal at ~30 ms, terminal at
    ~1.1 s. The old single poll returned False here and the caller then refused
    to exit -- with the orders already cancelled."""
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_DEADLINE_SEC", 1.0)
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_POLL_SEC", 0.01)

    calls = {"n": 0}

    def hist(_oid):
        calls["n"] += 1
        return _hist("OPEN") if calls["n"] < 4 else _hist("CANCELLED")

    ad = MagicMock()
    ad.get_order_history.side_effect = hist
    assert _bare(adapter=ad)._verify_cancelled(_resting_sl()) is True, (
        "a cancel that settles late must be accepted -- polling once at +30ms is "
        "what produced 3 of 3 CANCEL_FAILED"
    )
    assert calls["n"] >= 2, "it must actually re-read, not accept on one poll"


def test_f2_never_terminal_within_the_deadline_is_a_settled_negative(monkeypatch) -> None:
    """The deadline must still be able to say NO -- otherwise F2 would just
    rubber-stamp every cancel and the guard would be vacuous."""
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_DEADLINE_SEC", 0.05)
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_POLL_SEC", 0.01)
    ad = MagicMock()
    ad.get_order_history.return_value = _hist("OPEN")
    assert _bare(adapter=ad)._verify_cancelled(_resting_sl()) is False


def test_f2_a_failed_read_does_not_end_the_poll(monkeypatch) -> None:
    """A read that raises is not evidence the cancel failed. Before F2 the first
    exception returned False immediately."""
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_DEADLINE_SEC", 1.0)
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_POLL_SEC", 0.01)
    seq = [RuntimeError("boom"), RuntimeError("boom"), _hist("COMPLETE")]

    def hist(_oid):
        item = seq.pop(0) if seq else _hist("COMPLETE")
        if isinstance(item, Exception):
            raise item
        return item

    ad = MagicMock()
    ad.get_order_history.side_effect = hist
    assert _bare(adapter=ad)._verify_cancelled(_resting_sl()) is True


@pytest.mark.parametrize("terminal", ["CANCELLED", "REJECTED", "COMPLETE"])
def test_f2_all_three_terminal_states_accepted(monkeypatch, terminal) -> None:
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_DEADLINE_SEC", 0.2)
    monkeypatch.setattr(mod, "_CANCEL_SETTLE_POLL_SEC", 0.01)
    ad = MagicMock()
    ad.get_order_history.return_value = _hist(terminal)
    assert _bare(adapter=ad)._verify_cancelled(_resting_sl()) is True


# ── F1 · restore protection ──────────────────────────────────────────────────

def test_f1_restores_the_sl_with_its_ORIGINAL_parameters() -> None:
    """The stop must come back exactly as it was -- restored, not recomputed.
    Nothing in the resting row carries price/qty/side, so it has to be read back
    from the local orders row."""
    ad = MagicMock()
    ad.get_open_orders.return_value = []          # nothing resting
    ad.place_order.return_value = SimpleNamespace(broker_order_id="X1")
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]

    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")

    assert "RESTORED" in detail, detail
    kw = ad.place_order.call_args.kwargs
    assert kw["symbol"] == _SYM
    assert kw["side"] == "SELL"
    assert kw["qty"] == 1
    assert kw["order_type"] == "SL"
    assert kw["price"] == pytest.approx(616.95)
    assert kw["trigger_price"] == pytest.approx(620.07705225)
    assert kw["variety"] == "regular"


def test_f1_tag_identifies_the_trade_and_fits_the_broker_limit() -> None:
    """01-Jul BANSALWIRE: the emergency exit was REJECTED because the tag was 36
    chars (Zerodha's limit is 20). A restore that cannot be placed is not a
    restore."""
    ad = MagicMock()
    ad.get_open_orders.return_value = []
    ad.place_order.return_value = SimpleNamespace(broker_order_id="X1")
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]

    _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")

    tag = ad.place_order.call_args.kwargs["tag"]
    assert len(tag) <= 20, f"tag {tag!r} exceeds Zerodha's 20-char limit"
    assert _TRADE.startswith(tag) or tag == _TRADE, (
        f"tag {tag!r} must identify the trade -- a static tag would leave a fill "
        f"unmappable, which is how EXTERNAL_UNATTRIBUTED closures arise"
    )


def test_f1_is_idempotent_when_protection_already_rests() -> None:
    """A second protective leg is a real risk of its own. If one is already at the
    broker, restore must place NOTHING."""
    ad = MagicMock()
    ad.get_open_orders.return_value = [
        {"symbol": _SYM, "order_id": "999", "status": "TRIGGER PENDING",
         "transaction_type": "SELL", "quantity": 1, "price": 616.95,
         "trigger_price": 620.077},
    ]
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]

    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")

    ad.place_order.assert_not_called()
    assert "already resting" in detail, detail


def test_f1_an_unrelated_symbol_does_not_count_as_protection() -> None:
    """The idempotency check must be symbol-scoped, or one stock's stop would
    suppress another's restore."""
    ad = MagicMock()
    ad.get_open_orders.return_value = [
        {"symbol": "OTHERCO", "order_id": "999", "status": "TRIGGER PENDING",
         "trigger_price": 100.0},
    ]
    ad.place_order.return_value = SimpleNamespace(broker_order_id="X1")
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]

    _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")
    ad.place_order.assert_called_once()


def test_f1_unknown_broker_state_still_restores() -> None:
    """If the open-order read fails we do NOT know whether protection exists.
    Leaving a position genuinely unprotected is worse than a duplicate leg, and
    the reconciler's one-live-SL invariant catches duplicates. Same fail-safe
    G5b already documents for an unavailable snapshot."""
    ad = MagicMock()
    ad.get_open_orders.side_effect = RuntimeError("broker unreachable")
    ad.place_order.return_value = SimpleNamespace(broker_order_id="X1")
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]

    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")
    ad.place_order.assert_called_once()
    assert "RESTORED" in detail, detail


def test_f1_never_raises_even_if_the_restore_itself_fails() -> None:
    """Restore runs inside an already-failing path. If it throws, it would mask
    the original failure and skip the alert."""
    ad = MagicMock()
    ad.get_open_orders.return_value = []
    ad.place_order.side_effect = RuntimeError("rejected")
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]

    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")
    assert "RESTORE FAILED" in detail and "UNPROTECTED" in detail, detail


def test_f1_says_so_when_the_original_parameters_are_gone() -> None:
    """Silence would read as success. If the SL row cannot be found, the alert
    must carry that."""
    ad = MagicMock()
    ad.get_open_orders.return_value = []
    st = MagicMock()
    st.get_orders_for_trade.return_value = []

    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, _resting_sl(), "PASS_1")
    ad.place_order.assert_not_called()
    assert "RESTORE FAILED" in detail, detail


def test_f1_does_not_restore_the_tgt_leg() -> None:
    """Only the STOP is protection. Re-placing the target would add a second
    resting sell for no safety gain."""
    ad = MagicMock()
    ad.get_open_orders.return_value = []
    st = MagicMock()
    st.get_orders_for_trade.return_value = [_sl_order_row()]
    tgt_only = [{"trade_id": _TRADE, "symbol": _SYM, "leg": "TGT",
                 "variety": "regular", "order_id": "260903170310434"}]

    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, tgt_only, "PASS_1")
    ad.place_order.assert_not_called()
    assert "RESTORE FAILED" in detail, detail


def test_f1_never_raises_on_a_malformed_resting_row() -> None:
    """The contract is 'never raises', and the first version broke it: a resting
    row without a 'leg' key raised KeyError straight out of an ALREADY-FAILING
    path, which would mask the original failure and skip its alert. Caught by the
    existing suite, whose fakes use a narrower row shape."""
    ad = MagicMock()
    ad.get_open_orders.return_value = []
    st = MagicMock()
    st.get_orders_for_trade.return_value = []

    detail = _bare(adapter=ad, store=st)._restore_protection(
        _SYM, [{"order_id": "SL1", "variety": "regular"}], "PASS_2")  # no 'leg'

    assert isinstance(detail, str) and detail, "must return a detail, not raise"
    ad.place_order.assert_not_called()


def test_f1_survives_a_row_object_without_the_field() -> None:
    """Production rows are sqlite3.Row, which raises IndexError (not KeyError)
    for a missing column. _row_get must absorb both."""
    class Bare:  # no attributes, no __getitem__
        pass
    ad = MagicMock()
    ad.get_open_orders.return_value = []
    st = MagicMock()
    detail = _bare(adapter=ad, store=st)._restore_protection(_SYM, [Bare()], "PASS_1")
    assert isinstance(detail, str) and detail
    ad.place_order.assert_not_called()
