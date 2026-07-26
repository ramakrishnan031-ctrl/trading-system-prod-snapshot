"""tests/unit/test_w8_closure_source_writer.py -- CW-B (26-Jul-2026).

W8 (P3-r10): trades.closure_source + trades.exit_mechanism, written at the SINGLE
central finalizer.

⭐ WHY ONE SITE AND NOT FIVE. The design called for writers on five own-exit paths.
Reading the code first showed that is unnecessary: `OrderManager.close_trade` has
exactly ONE caller (`order_placer.py:2359`) and already takes an exit_reason
VALIDATED against _VALID_EXIT_REASONS, and `order_placer._LEG_TO_EXIT_REASON` maps
the SL/TGT/EOD legs onto exactly three of those reasons. So the derivation is total
at one site and cannot drift, instead of five copies that can.

⚠️ MANUAL_CLOSE IS DELIBERATELY UNMAPPED. _LEG_TO_EXIT_REASON never produces it, so
mapping it would be inventing an answer for a case that does not arise on this path.
It writes NULL -- "we do not know" -- which is the honest value, and per
docs/closure_source_contract.md no reader may treat NULL as a value. A caller that
genuinely knows (the kill path, whose exit fills arrive as ordinary legs) passes
closure_source explicitly.

PARITY (Rule #5): OrderManager is mode-agnostic -- it holds no mode flag and takes
no mode argument, so paper and live traverse this identical code with identical
inputs. There is nothing mode-specific to assert; the parity claim is structural.

RED-first: every assertion here fails on pre-v45 code, where the two columns do not
exist at all (sqlite raises "no such column").

Run: python -m pytest tests/unit/test_w8_closure_source_writer.py -v
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.closure_source import (
    CLOSURE_SOURCES, MECH_GTT, MECH_LIMIT, MECH_MARKET,
    OWN_EOD, OWN_KILL, OWN_SL, OWN_TGT,
)
from core.state_store import StateStore
from orders.order_manager import OrderManager

_SCHEMA_PATH = Path(__file__).parent.parent.parent / "core" / "schema.sql"


def _open_trade(tmp_path: Path):
    """A real StateStore + a trade in OPEN state, ready to close."""
    store = StateStore(tmp_path / "w8.db", _SCHEMA_PATH)
    om = OrderManager(state_store=store, logger=logging.getLogger("test_w8"))
    now = "2026-04-16T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("sig_w8", "RELIANCE", "SCANNER", "vwap_bounce_long", now, now,
             "2026-04-16T09:35:00+05:30", "TRADED", "fp_w8", "2026-04-16"),
        )
    tid = om.create_trade(
        signal_id="sig_w8", symbol="RELIANCE", direction="LONG",
        strategy="vwap_bounce_long", sector=None, qty=10,
        entry_target_price=2500.0, sl_initial=2475.0, tgt_initial=2550.0,
        order_protocol="LIMIT_TRIPLE", margin_reserved=5000.0, risk_amount=250.0,
    )
    om.record_entry_fill(trade_id=tid, avg_fill_price=2500.0, qty_filled=10,
                         filled_at=now)
    return store, om, tid


def _row(store: StateStore, tid: str) -> dict:
    return dict(store.fetch_one(
        "SELECT closure_source, exit_mechanism, status, exit_reason "
        "FROM trades WHERE trade_id = ?", (tid,)))


# ── the derivation is total over the reasons that actually occur ───────────────

@pytest.mark.parametrize("exit_reason,expected", [
    ("TGT_HIT", OWN_TGT),
    ("SL_HIT", OWN_SL),
    ("EOD_SQUAREOFF", OWN_EOD),
])
def test_closure_source_derived_from_exit_reason(tmp_path: Path,
                                                 exit_reason: str,
                                                 expected: str) -> None:
    store, om, tid = _open_trade(tmp_path)
    try:
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason=exit_reason, gross_pnl=500.0, charges=20.0)
        r = _row(store, tid)
        assert r["closure_source"] == expected
        assert r["status"] == "CLOSED"
    finally:
        store.close()


def test_manual_close_writes_null_rather_than_guessing(tmp_path: Path) -> None:
    """⚠️ NULL is the honest value. Mapping MANUAL_CLOSE would invent an answer for
    a case order_placer's leg taxonomy never produces."""
    store, om, tid = _open_trade(tmp_path)
    try:
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason="MANUAL_CLOSE", gross_pnl=500.0, charges=20.0)
        assert _row(store, tid)["closure_source"] is None
    finally:
        store.close()


def test_explicit_closure_source_overrides_the_derivation(tmp_path: Path) -> None:
    """OWN_KILL has no exit_reason of its own -- the kill path's exit fills arrive
    as ordinary legs -- so a caller that knows declares it."""
    store, om, tid = _open_trade(tmp_path)
    try:
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason="SL_HIT", gross_pnl=500.0, charges=20.0,
                       closure_source=OWN_KILL)
        assert _row(store, tid)["closure_source"] == OWN_KILL
    finally:
        store.close()


# ── the second axis ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("mech", [MECH_LIMIT, MECH_MARKET, MECH_GTT])
def test_exit_mechanism_is_persisted_independently(tmp_path: Path, mech: str) -> None:
    """The two axes are orthogonal: the same reason can arrive by any mechanism."""
    store, om, tid = _open_trade(tmp_path)
    try:
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason="TGT_HIT", gross_pnl=500.0, charges=20.0,
                       exit_mechanism=mech)
        r = _row(store, tid)
        assert r["exit_mechanism"] == mech
        assert r["closure_source"] == OWN_TGT, "the reason axis is unaffected"
    finally:
        store.close()


def test_exit_mechanism_defaults_to_null_when_not_supplied(tmp_path: Path) -> None:
    store, om, tid = _open_trade(tmp_path)
    try:
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason="TGT_HIT", gross_pnl=500.0, charges=20.0)
        assert _row(store, tid)["exit_mechanism"] is None
    finally:
        store.close()


# ── ⚠️ neither axis may be written with an off-vocabulary value ────────────────

def test_invalid_closure_source_is_refused(tmp_path: Path) -> None:
    store, om, tid = _open_trade(tmp_path)
    try:
        with pytest.raises(ValueError, match="closure_source"):
            om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                           exit_reason="TGT_HIT", gross_pnl=500.0, charges=20.0,
                           closure_source="BROKER_RMS")
        assert _row(store, tid)["status"] != "CLOSED", \
            "a rejected value must not have closed the trade"
    finally:
        store.close()


def test_invalid_exit_mechanism_is_refused(tmp_path: Path) -> None:
    store, om, tid = _open_trade(tmp_path)
    try:
        with pytest.raises(ValueError, match="exit_mechanism"):
            om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                           exit_reason="TGT_HIT", gross_pnl=500.0, charges=20.0,
                           exit_mechanism="SMART_ROUTE")
    finally:
        store.close()


def test_every_derived_value_is_in_the_canonical_vocabulary(tmp_path: Path) -> None:
    """Guards against the map drifting away from core/closure_source.py."""
    from orders.order_manager import _EXIT_REASON_TO_CLOSURE_SOURCE
    assert set(_EXIT_REASON_TO_CLOSURE_SOURCE.values()) <= CLOSURE_SOURCES


def test_backfill_is_null_for_a_row_closed_by_another_path(tmp_path: Path) -> None:
    """⭐ 3.3: existing rows get NULL. Proven here for the mark_trade_manually_closed
    path, which does not write the axes at all -- the same shape the v45 migration
    gives all 423 historical rows."""
    store, om, tid = _open_trade(tmp_path)
    try:
        store.mark_trade_manually_closed(tid)
        r = _row(store, tid)
        assert r["status"] == "CLOSED_MANUAL"
        assert r["closure_source"] is None and r["exit_mechanism"] is None
    finally:
        store.close()
