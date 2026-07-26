"""tests/unit/test_check1_deferral.py -- CW-D: the mid-fill DEFERRAL (26-Jul-2026).

§C made CHECK1 say the right thing. §D fixes the one thing left: the CAPITAL
TIMING. CHECK1 polls positions and beats our own fill callback by ~0.9 s, so it
can release capital against a verdict formed before the callback that owns the
close has even arrived.

⭐ THE BOUND IS WALL-CLOCK SECONDS, NOT CYCLES. A cycle count is a proxy for
elapsed time whose meaning changes silently the day poll_interval_sec is retuned.

⭐⭐ 0 IS A TRUE NO-OP, AND THAT IS ASSERTED STRUCTURALLY, NOT APPROXIMATELY.
`test_bound_zero_...` replaces the §D bookkeeping with a tripwire that raises on
any read OR write, and the clock with a callable that raises. If the pre-§D path
is not the one taken, the test does not "differ" -- it fails naming what touched
what. That is what makes Monday readable and the config flip a decision rather
than a leap.

⚠️ A BOUND THAT CAN EXPIRE MUST LOG ITS EXPIRY. An expired deferral is the case
where THIS DESIGN WAS WRONG -- the callback we waited for never came -- and a
wrong thing that is silent is this project's signature failure.

Run: python -m pytest tests/unit/test_check1_deferral.py -v
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.closure_source import EXTERNAL_UNATTRIBUTED, OWN_TGT
from tests.unit.test_order_reconciler import (
    _cancel_res, _insert_order, _insert_trade, _make_reconciler, _make_store,
)

#: The broker's exact refusal shape: our own leg is filling RIGHT NOW.
_MID_FILL = "Order cannot be cancelled as it is being processed"


def _trade_row(trade_id="t1", symbol="IRFC", qty=10):
    r = MagicMock()
    r.__getitem__ = lambda _s, k: {
        "trade_id": trade_id, "symbol": symbol, "direction": "LONG",
        "qty_filled": qty, "entry_actual_price": 100.0, "product": "MIS",
        "signal_id": "sig1",
    }[k]
    return r


class _Tripwire(dict):
    """§D's bookkeeping, booby-trapped. At bound 0 NOTHING may touch it."""

    def _boom(self, *_a, **_k):
        raise AssertionError(
            "bound 0 touched the §D deferral state -- it is NOT a true no-op")

    __getitem__ = __setitem__ = __delitem__ = __contains__ = _boom
    get = pop = setdefault = update = _boom


def _no_clock():
    raise AssertionError("bound 0 read the clock -- it is NOT a true no-op")


class _Case:
    """One CHECK1 subject, driven cycle by cycle against a real store."""

    def __init__(self, tmp_path: Path, *, bound: float, legs, cancel_reason="",
                 cancel_ok=True, broker_trades=None):
        self.store = _make_store(tmp_path)
        _insert_trade(self.store, "t1", symbol="IRFC", status="OPEN",
                      entry_actual_price=100.0)
        for oid, leg, status in legs:
            _insert_order(self.store, oid, "t1", leg=leg, status=status,
                          trigger_price=99.0)
        adapter = MagicMock()
        adapter.cancel_order.return_value = _cancel_res(cancel_ok, cancel_reason)
        adapter.get_trades.return_value = broker_trades or []
        self.adapter = adapter
        self.notifier = MagicMock()
        self.rec = _make_reconciler(self.store, adapter=adapter)
        self.rec._cfg.check1_mid_fill_defer_sec = bound
        self.rec._fm.release_used.return_value = SimpleNamespace(pnl_delta=5.0)
        self.rec._notifier = self.notifier
        self.rec._mode = "LIVE"
        self.now = 1_000.0
        self.rec._monotonic = lambda: self.now

    # a SECOND reconciler over the SAME store == a process restart: the DB
    # survives, the in-memory deferral does not.
    def restart(self):
        rec = _make_reconciler(self.store, adapter=self.adapter)
        rec._cfg.check1_mid_fill_defer_sec = self.rec._cfg.check1_mid_fill_defer_sec
        rec._fm.release_used.return_value = SimpleNamespace(pnl_delta=5.0)
        rec._notifier = self.notifier
        rec._mode = "LIVE"
        rec._monotonic = lambda: self.now
        self.rec = rec
        return rec

    def cycle(self):
        return self.rec._check1_manual_close(_trade_row())

    def row(self) -> dict:
        return dict(self.store.fetch_one(
            "SELECT status, closure_source FROM trades WHERE trade_id='t1'"))

    def severities(self) -> list:
        return [c.kwargs.get("severity") for c in self.notifier.send.call_args_list]

    @property
    def releases(self) -> int:
        return self.rec._fm.release_used.call_count

    def close(self):
        self.store.close()


# ── ⭐⭐ 1.2: PROVE 0 IS A TRUE NO-OP ─────────────────────────────────────────

def test_bound_zero_takes_the_pre_fix_path_and_touches_nothing(tmp_path: Path) -> None:
    """⭐ Not "approximately today's behaviour" -- the pre-§D path, proved by
    booby-trapping everything §D owns. Mid-fill evidence is present, which is the
    ONLY input that would make bound > 0 defer, so if the gate is reachable at all
    at bound 0 this test fires."""
    c = _Case(tmp_path, bound=0.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    c.rec._check1_deferred_since = _Tripwire()
    c.rec._monotonic = _no_clock
    try:
        action = c.cycle()
        assert action.check_name == "MANUAL_CLOSE"   # not DEFERRED
        assert c.row()["status"] == "CLOSED_MANUAL"
        assert c.row()["closure_source"] == OWN_TGT
        assert c.releases == 1
        assert c.severities() == ["INFO"]
    finally:
        c.close()


# ── the deferral itself ───────────────────────────────────────────────────────

def test_mid_fill_defers_and_finalizes_absolutely_nothing(tmp_path: Path) -> None:
    """⭐ The point of §D. It does not mark, does not release, does not alert --
    order_monitor owns the leg, which is what the comment at _cancel_orphaned_
    orders_for_trade already said should happen."""
    c = _Case(tmp_path, bound=90.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        action = c.cycle()
        assert action.check_name == "MANUAL_CLOSE_DEFERRED"
        assert action.tier == "COSMETIC"
        assert c.row()["status"] == "OPEN", "the trade must NOT be claimed"
        assert c.row()["closure_source"] is None
        assert c.releases == 0, "capital must stay reserved while deferred"
        assert c.notifier.send.call_count == 0, "a deferral is not an event to alert"
    finally:
        c.close()


def test_deferral_does_not_fire_without_mid_fill_evidence(tmp_path: Path) -> None:
    """§D is NARROW by design: only the broker's own "being processed" defers.
    A leg that is already COMPLETE locally finalizes immediately, at bound > 0
    exactly as at bound 0 (design table rows 1-2: finalize, attribute)."""
    c = _Case(tmp_path, bound=90.0, legs=[("tgt1", "TGT", "COMPLETE")])
    try:
        action = c.cycle()
        assert action.check_name == "MANUAL_CLOSE"
        assert c.row()["closure_source"] == OWN_TGT
        assert c.releases == 1
        assert c.severities() == ["INFO"]
    finally:
        c.close()


# ── ⚠️ 1.3: A BOUND THAT CAN EXPIRE MUST LOG ITS EXPIRY ──────────────────────

def test_expiry_finalizes_alerts_and_says_the_design_was_wrong(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """⭐ The callback never came. Mid-fill is a claim about NOW and it has gone
    stale, so it stops counting as evidence -- the verdict falls to
    EXTERNAL_UNATTRIBUTED at CRITICAL (design row 4), and the expiry itself is
    logged, because a wrong thing that is silent is this project's signature
    failure."""
    c = _Case(tmp_path, bound=5.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        assert c.cycle().check_name == "MANUAL_CLOSE_DEFERRED"
        c.now += 6.0
        with caplog.at_level(logging.WARNING, logger="order_reconciler"):
            action = c.cycle()
        assert action.check_name == "MANUAL_CLOSE"
        assert c.row()["status"] == "CLOSED_MANUAL"
        assert c.row()["closure_source"] == EXTERNAL_UNATTRIBUTED
        assert c.severities() == ["CRITICAL"]
        assert any("DEFERRAL EXPIRED UNRESOLVED" in r.getMessage()
                   for r in caplog.records), "an expiry that is silent is the defect"
    finally:
        c.close()


def test_a_deferral_below_the_bound_keeps_deferring(tmp_path: Path) -> None:
    """The bound is TOTAL elapsed wall clock from the FIRST deferral, not a
    per-cycle reset -- otherwise a leg that keeps reporting "being processed"
    would defer for ever."""
    c = _Case(tmp_path, bound=10.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        for step in (0.0, 3.0, 3.0):
            c.now += step
            assert c.cycle().check_name == "MANUAL_CLOSE_DEFERRED"
        c.now += 5.0                                   # total 11.0 > 10.0
        assert c.cycle().check_name == "MANUAL_CLOSE"
        assert c.releases == 1
    finally:
        c.close()


# ── ⚠️ 1.4: EXACTLY ONE PATH RELEASES CAPITAL, AT BOTH BOUNDS ────────────────

def test_exactly_one_release_at_bound_zero(tmp_path: Path) -> None:
    c = _Case(tmp_path, bound=0.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        c.cycle()
        assert c.releases == 1
        c.cycle()                                      # re-detected next cycle
        assert c.releases == 1, "the claim still guards the second pass"
    finally:
        c.close()


def test_at_bound_gt_zero_our_own_exit_path_is_the_one_that_releases(
        tmp_path: Path) -> None:
    """⭐ THE CAPITAL-PATH CLAIM. Deferral changes WHICH path releases, never HOW
    MANY. Here the fill callback lands during the deferral window (driven through
    the REAL OrderManager.close_trade, the same finalizer order_placer uses), and
    CHECK1 -- which would have released at bound 0 -- releases nothing at all."""
    c = _Case(tmp_path, bound=90.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        assert c.cycle().check_name == "MANUAL_CLOSE_DEFERRED"
        assert c.releases == 0

        # our own exit path arrives, exactly as order_placer._handle_exit_fill does
        c.rec._order_mgr.close_trade(
            trade_id="t1", exit_price=105.0, exit_qty=10, exit_reason="TGT_HIT",
            gross_pnl=50.0, charges=2.0,
        )
        c.now += 15.0
        action = c.cycle()                             # the next reconcile cycle
        assert action.tier == "COSMETIC"
        assert c.releases == 0, "CHECK1 must not release what our own path closed"
        assert c.row()["status"] == "CLOSED"
        assert c.row()["closure_source"] == OWN_TGT, "written by the ONE finalizer"
    finally:
        c.close()


def test_expiry_releases_exactly_once_and_a_rerun_does_not_double_release(
        tmp_path: Path) -> None:
    c = _Case(tmp_path, bound=5.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        # asserted, so this cannot pass vacuously on a build where nothing defers
        assert c.cycle().check_name == "MANUAL_CLOSE_DEFERRED"
        assert c.releases == 0
        c.now += 6.0
        assert c.cycle().check_name == "MANUAL_CLOSE"
        assert c.releases == 1
        c.now += 15.0
        c.cycle()
        assert c.releases == 1, "the claim is still the double-release guard"
    finally:
        c.close()


# ── ⚠️ 1.5: WHAT IF THE PROCESS DIES MID-DEFERRAL? ───────────────────────────

def test_a_process_death_mid_deferral_cannot_strand_capital(tmp_path: Path) -> None:
    """⚠️ THE RESTART-SAFETY QUESTION §D RAISES AND §C DID NOT.

    A trade is deferred, its capital is still reserved, and the process stops. The
    deferral clock is in memory and dies with it. Does the next boot recover the
    trade, or is capital reserved against a position that no longer exists?

    ANSWER, asserted here: it recovers, and losing the clock can only ever cost ONE
    more bounded window. The trade is still OPEN in the DB, so boot rehydrates it
    and the very next reconcile cycle re-enters CHECK1 with an empty map -- which
    starts a NEW window, never an infinite one. The failure direction is "a few
    more seconds", not "capital stranded"."""
    c = _Case(tmp_path, bound=5.0, legs=[("tgt1", "TGT", "OPEN")],
              cancel_ok=False, cancel_reason=_MID_FILL)
    try:
        assert c.cycle().check_name == "MANUAL_CLOSE_DEFERRED"
        assert c.row()["status"] == "OPEN"

        c.restart()                                    # process death + reboot
        assert c.releases == 0, "the fresh window must not skip straight to release"
        assert c.cycle().check_name == "MANUAL_CLOSE_DEFERRED"
        c.now += 6.0
        assert c.cycle().check_name == "MANUAL_CLOSE"
        assert c.releases == 1, "capital is recovered, exactly once"
        assert c.row()["status"] == "CLOSED_MANUAL"
    finally:
        c.close()
