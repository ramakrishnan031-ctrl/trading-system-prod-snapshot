"""tests/unit/test_check1_classified_close.py -- CW-C wiring (26-Jul-2026).

CHECK1 now GATHERS evidence, CLASSIFIES, then ACTS -- and emits the alert that
matches the evidence instead of asserting "Position closed externally" from the one
fact that the symbol vanished from get_positions().

⭐ 1.4 IS THE POINT. Because CHECK1 wins the race against our own exit path,
order_placer._handle_exit_fill hits its double-close early return (:2374) and its
INFO "TARGET HIT" is never sent. So the false CRITICAL did not merely add noise --
it REPLACED the good alert. CHECK1 emitting the correct INFO itself is what lets §C
ship without §D:

    bound 0    -> reconciler finalizes AND alerts (INFO)   -- capital timing unchanged
    bound > 0  -> reconciler defers; order_placer alerts   -- (§D, not built here)

⚠️ EXACTLY ONE ALERT in each case. A double alert is this design's obvious failure
mode and is excluded here by test, not by reasoning.

⚠️ THE FIRST PRINCIPLE: suppression requires POSITIVE evidence. A degraded source
(orders unreadable, cancel reason unrecognised, sources disagreeing) still fires
CRITICAL even when a COMPLETE own leg is present.

Run: python -m pytest tests/unit/test_check1_classified_close.py -v
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.closure_source import EXTERNAL_UNATTRIBUTED, OWN_SL, OWN_TGT
from tests.unit.test_order_reconciler import (
    _cancel_res, _insert_order, _insert_trade, _make_reconciler, _make_store,
)

_LOG = logging.getLogger("test_check1_classified")


def _trade_row(trade_id="t1", symbol="IRFC", qty=10, entry_price=100.0):
    r = MagicMock()
    r.__getitem__ = lambda _s, k: {
        "trade_id": trade_id, "symbol": symbol, "direction": "LONG",
        "qty_filled": qty, "entry_actual_price": entry_price, "product": "MIS",
        "signal_id": "sig1",
    }[k]
    return r


def _severities(notifier) -> list[str]:
    return [c.kwargs.get("severity") for c in notifier.send.call_args_list]


#: Which `orders` read to break, for the planted-failure tests. Discriminated on
#: the SQL itself so the plant sits at the DB boundary -- the real `except` handler
#: in order_reconciler runs, rather than a mocked-out verdict.
_EXIT_LEG_READ = "exit_legs"        # SELECT ... leg IN ('SL','TGT','EOD')
_ORPHAN_LOOKUP = "orphan_lookup"    # SELECT ... status NOT IN (...)


def _plant_orders_read_failure(store, which: str) -> None:
    """Make exactly ONE of CHECK1's two `orders` reads raise."""
    real = store.fetch_all

    def _wrapped(sql, *a, **kw):
        text = " ".join(str(sql).split())
        is_orphan = "status NOT IN" in text
        is_exit_legs = "'EOD'" in text
        if (which == _ORPHAN_LOOKUP and is_orphan) or (
                which == _EXIT_LEG_READ and is_exit_legs):
            raise RuntimeError("database disk image is malformed")
        return real(sql, *a, **kw)

    store.fetch_all = _wrapped


def _run_check1(tmp_path: Path, *, legs, cancel_reason=None, cancel_ok=True,
                broker_trades=None, trades_raises=False, symbol="IRFC",
                qty=10, entry_price=100.0, plant_read_failure=None):
    """Drive the REAL _check1_manual_close against a real store."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol=symbol, status="OPEN")
    for oid, leg, status in legs:
        _insert_order(store, oid, "t1", leg=leg, status=status, trigger_price=99.0)
    if plant_read_failure:
        # After the inserts: planting earlier would break the fixture, not the code.
        _plant_orders_read_failure(store, plant_read_failure)

    adapter = MagicMock()
    adapter.cancel_order.return_value = _cancel_res(cancel_ok, cancel_reason or "")
    if trades_raises:
        adapter.get_trades.side_effect = RuntimeError("broker down")
    else:
        adapter.get_trades.return_value = broker_trades or []

    notifier = MagicMock()
    rec = _make_reconciler(store, adapter=adapter)
    # release_used must return a REAL pnl_delta: the alert block formats it with
    # :+.2f, and a MagicMock there raises before any alert is sent.
    rec._fm.release_used.return_value = SimpleNamespace(pnl_delta=5.0)
    rec._notifier = notifier
    rec._mode = "LIVE"
    action = rec._check1_manual_close(
        _trade_row(symbol=symbol, qty=qty, entry_price=entry_price))
    row = store.fetch_one(
        "SELECT closure_source, status FROM trades WHERE trade_id='t1'")
    return store, notifier, action, dict(row)


def _bt(order_id, qty=10, price=105.0, symbol="IRFC"):
    return {"trade_id": "T", "order_id": order_id, "tradingsymbol": symbol,
            "transaction_type": "SELL", "quantity": qty, "average_price": price,
            "fill_timestamp": ""}


# ── ⭐ 1.4: exactly one alert, and it is the RIGHT one ─────────────────────────

def test_own_leg_close_emits_exactly_one_INFO_not_a_false_critical(tmp_path: Path) -> None:
    """The WAAREERTL shape: our own TGT filled. Today this sends a CRITICAL saying
    'Position closed externally' AND the real TARGET HIT is swallowed."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE")])
    try:
        sev = _severities(notifier)
        assert sev == ["INFO"], f"expected exactly one INFO, got {sev}"
        assert row["closure_source"] == OWN_TGT
        title = notifier.send.call_args.kwargs["title"]
        assert "RMS/MANUAL CLOSE" not in title
    finally:
        store.close()


def test_own_sl_close_is_attributed_to_the_sl_leg(tmp_path: Path) -> None:
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("sl1", "SL", "COMPLETE")])
    try:
        assert _severities(notifier) == ["INFO"]
        assert row["closure_source"] == OWN_SL
    finally:
        store.close()


def test_mid_fill_refusal_is_treated_as_our_own_close(tmp_path: Path) -> None:
    """⭐ The signal D1 rescued: the broker refused the cancel BECAUSE our leg was
    filling. That is positive evidence, and it used to be logged and discarded."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "OPEN")],
        cancel_ok=False, cancel_reason="Order cannot be cancelled as it is being processed")
    try:
        assert _severities(notifier) == ["INFO"]
        assert row["closure_source"] == OWN_TGT
    finally:
        store.close()


def test_broker_order_id_identifies_our_leg(tmp_path: Path) -> None:
    """Rung 1: get_trades() names the order that filled — plumbed out of
    _resolve_exit_price, which fetched it and kept only average_price."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "OPEN")], broker_trades=[_bt("tgt1")])
    try:
        assert _severities(notifier) == ["INFO"]
        assert row["closure_source"] == OWN_TGT
    finally:
        store.close()


# ── ⭐ the true-positive path must survive ────────────────────────────────────

def test_genuine_external_close_still_fires_exactly_one_CRITICAL(tmp_path: Path) -> None:
    """⭐ THE ACCEPTANCE TEST. No own leg accounts for it — the shape of the 6
    CLOSED_MANUAL rows that are the candidate true positives (§3.2)."""
    store, notifier, _a, row = _run_check1(tmp_path, legs=[])
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        assert "closed externally" in notifier.send.call_args.kwargs["body"]
    finally:
        store.close()


def test_cancelled_legs_do_not_account_for_the_close(tmp_path: Path) -> None:
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("sl1", "SL", "CANCELLED")])
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
    finally:
        store.close()


def test_unrecognised_cancel_reason_forces_CRITICAL_despite_a_complete_leg(tmp_path: Path) -> None:
    """⚠️ THE FIRST PRINCIPLE. A COMPLETE own leg is present, but one cancel failed
    for a reason we do not recognise — that is an unknown, and an unknown never
    suppresses the CRITICAL."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE"), ("sl1", "SL", "OPEN")],
        cancel_ok=False, cancel_reason="Network unreachable")
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
    finally:
        store.close()


def test_sources_disagreeing_forces_CRITICAL_not_the_higher_rung(tmp_path: Path) -> None:
    """⚠️ THE CONTRADICTION RULE end-to-end: the broker names our SL while our local
    TGT row says COMPLETE. Precedence orders sources that are SILENT; it never
    overrules one that SPOKE."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE"), ("sl1", "SL", "OPEN")],
        broker_trades=[_bt("sl1")])
    try:
        assert _severities(notifier) == ["CRITICAL"]
        assert row["closure_source"] == EXTERNAL_UNATTRIBUTED
        assert "SOURCES DISAGREED" in notifier.send.call_args.kwargs["body"]
    finally:
        store.close()


def test_broker_read_failure_still_classifies_from_the_local_leg(tmp_path: Path) -> None:
    """§3.1: get_trades() failing is a SILENT source, not a corrupt one. Rung 3
    (local COMPLETE alone) still decides — which is also the only rung paper ever
    reaches, since paper's get_trades() returns []."""
    store, notifier, _a, row = _run_check1(
        tmp_path, legs=[("tgt1", "TGT", "COMPLETE")], trades_raises=True)
    try:
        assert _severities(notifier) == ["INFO"]
        assert row["closure_source"] == OWN_TGT
    finally:
        store.close()


# ── the money invariant is untouched ──────────────────────────────────────────

def test_capital_is_released_exactly_once_regardless_of_verdict(tmp_path: Path) -> None:
    """⚠️ CHECK1 claims the trade FIRST (mark_trade_manually_closed, idempotent),
    so exactly one path releases. The verdict changes the LABEL and the SEVERITY,
    never the number of releases."""
    for legs in ([("tgt1", "TGT", "COMPLETE")], []):
        store = _make_store(tmp_path / f"c{len(legs)}")
        _insert_trade(store, "t1", symbol="IRFC", status="OPEN")
        for oid, leg, status in legs:
            _insert_order(store, oid, "t1", leg=leg, status=status, trigger_price=99.0)
        adapter = MagicMock()
        adapter.cancel_order.return_value = _cancel_res(True, "")
        adapter.get_trades.return_value = []
        rec = _make_reconciler(store, adapter=adapter)
        rec._fm.release_used.return_value = SimpleNamespace(pnl_delta=5.0)
        rec._notifier = MagicMock()
        rec._check1_manual_close(_trade_row())
        assert rec._fm.release_used.call_count == 1, "exactly one capital release"
        # second pass: the claim is already terminal -> no second release
        rec._check1_manual_close(_trade_row())
        assert rec._fm.release_used.call_count == 1, "re-running must not double-release"
        store.close()
