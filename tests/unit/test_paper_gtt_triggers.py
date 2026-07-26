"""
tests/unit/test_paper_gtt_triggers.py — a paper GTT can now FIRE.

Before this, nothing ever wrote a paper GTT status other than "active", so
CncGttMonitor's primary path (triggered + flat -> GTT_EXIT) and its F6 re-protect
branch were unreachable by a running paper session. Every CNC exit runs through a
GTT, so "paper-proven" -- Slice 2.5's stated gate for going live with real capital
-- could not cover the CNC exit path at all.

⭐ THE TELL: the 25 tests credited to CncGttMonitor + FIX-183 reach `triggered` by
writing the adapter's PRIVATE dict, `env.adapter._paper_gtts[gid]["status"]`. A test
that must reach past the public API to create a state is naming a gap. The last test
in this file is the proof the fix is real: the same state, reached through the public
path, with no cheating.

⛔ WHAT IS DELIBERATELY NOT MODELLED is asserted here too (see the "limits" section),
because a paper path that passes WRONGLY is worse than one that cannot run.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from broker.zerodha_adapter import ZerodhaAdapter


# ── the pure trigger rule ─────────────────────────────────────────────────────
# Driven directly: no adapter, no quote provider, no clock, no thread.

_TRIG = ZerodhaAdapter._gtt_triggered_leg


def test_price_between_the_bounds_does_not_fire():
    assert _TRIG([90.0, 110.0], 100.0) is None
    assert _TRIG([90.0, 110.0], 90.01) is None
    assert _TRIG([90.0, 110.0], 109.99) is None


def test_crossing_the_sl_bound_fires_leg_0():
    assert _TRIG([90.0, 110.0], 89.99) == 0
    assert _TRIG([90.0, 110.0], 50.0) == 0


def test_crossing_the_tgt_bound_fires_leg_1():
    assert _TRIG([90.0, 110.0], 110.01) == 1
    assert _TRIG([90.0, 110.0], 200.0) == 1


def test_the_bounds_are_INCLUSIVE_like_zerodha():
    """Zerodha fires when LTP reaches the trigger, not only past it. An exclusive
    comparison would silently under-protect at exactly the stop price."""
    assert _TRIG([90.0, 110.0], 90.0) == 0
    assert _TRIG([90.0, 110.0], 110.0) == 1


def test_outside_BOTH_bounds_the_protective_leg_wins():
    """A poll can observe a price outside both bounds; a tick stream would have seen
    one crossed first. When we cannot know the order, the SL must win -- guessing TGT
    on a collapsing price would book a profit that never existed."""
    assert _TRIG([100.0, 100.0], 100.0) == 0


def test_a_malformed_trigger_list_never_fires():
    """Fail CLOSED: an unparseable condition must not be read as a crossing."""
    for bad in ([], [90.0], None, ["x", "y"], [None, None]):
        assert _TRIG(bad, 100.0) is None


# ── the adapter path ──────────────────────────────────────────────────────────

def _paper_adapter(ltp=None):
    """A paper adapter whose quote_provider answers with `ltp` (None = no data)."""
    a = ZerodhaAdapter.__new__(ZerodhaAdapter)          # no broker, no login
    a._paper = True
    a._paper_gtts = {}
    a._paper_gtts_lock = threading.Lock()
    a._paper_gtt_seq = 0
    a._log = MagicMock()
    a._quote_provider = (
        None if ltp is None
        else (lambda syms: {s: SimpleNamespace(last_price=ltp) for s in syms})
    )
    return a


def _seed_gtt(adapter, symbol="IRFC", sl=90.0, tgt=110.0, last=100.0):
    gid = "1001"
    adapter._paper_gtts[gid] = ZerodhaAdapter._paper_gtt_record(
        gid, symbol, [sl, tgt], last,
        adapter._gtt_legs("SELL", 10, sl, tgt, "CNC"), status="active")
    return gid


def test_an_untouched_gtt_stays_active():
    a = _paper_adapter(ltp=100.0)
    gid = _seed_gtt(a)
    assert a.get_gtt(gid)["status"] == "active"
    assert [g["status"] for g in a.get_gtts()] == ["active"]


def test_get_gtts_fires_the_gtt_when_price_crossed_the_stop():
    """RED ON OLD: this is the assertion that could not be made before -- the public
    read path returning `triggered` without anyone writing the private dict."""
    a = _paper_adapter(ltp=85.0)
    _seed_gtt(a)
    assert [g["status"] for g in a.get_gtts()] == ["triggered"]


def test_get_gtt_fires_it_too_and_records_the_price_that_did_it():
    a = _paper_adapter(ltp=120.0)
    gid = _seed_gtt(a)
    rec = a.get_gtt(gid)
    assert rec["status"] == "triggered"
    assert rec["condition"]["last_price"] == 120.0     # the observation, not the stale seed


def test_a_triggered_gtt_is_not_re_evaluated():
    """Once triggered it is terminal: a later quote inside the bounds must not
    un-trigger it. Zerodha has no path back to active either."""
    a = _paper_adapter(ltp=85.0)
    gid = _seed_gtt(a)
    assert a.get_gtt(gid)["status"] == "triggered"
    a._quote_provider = lambda syms: {s: SimpleNamespace(last_price=100.0) for s in syms}
    assert a.get_gtt(gid)["status"] == "triggered"


def test_no_quote_provider_means_no_trigger_not_a_zero_price_trigger():
    """⭐ FIX-001's rule, inherited: _fetch_ltp returns None (never 0.0) on no data.
    If a missing quote read as 0.0 it would be below every stop and fire EVERY SL in
    the book -- the worst possible failure for a protective mechanism."""
    a = _paper_adapter(ltp=None)
    gid = _seed_gtt(a)
    assert a.get_gtt(gid)["status"] == "active"


def test_a_broken_quote_provider_does_not_break_the_read_path():
    a = _paper_adapter(ltp=100.0)
    a._quote_provider = MagicMock(side_effect=RuntimeError("kite down"))
    gid = _seed_gtt(a)
    assert a.get_gtt(gid)["status"] == "active"        # degrades, does not raise


def test_one_quote_per_symbol_even_with_several_gtts():
    """The read path is called every reconcile cycle; it must not fan out one network
    call per GTT for the same symbol."""
    a = _paper_adapter(ltp=100.0)
    provider = MagicMock(side_effect=lambda syms: {s: SimpleNamespace(last_price=100.0)
                                                   for s in syms})
    a._quote_provider = provider
    for i, sym in enumerate(["IRFC", "IRFC", "IDEA"]):
        gid = str(2000 + i)
        a._paper_gtts[gid] = ZerodhaAdapter._paper_gtt_record(
            gid, sym, [90.0, 110.0], 100.0,
            a._gtt_legs("SELL", 10, 90.0, 110.0, "CNC"), status="active")
    a.get_gtts()
    assert provider.call_count == 2                    # IRFC once, IDEA once -- not 3


def test_live_mode_is_untouched():
    """PARITY, in the direction that matters: the settle pass must be inert in live,
    where Zerodha owns the GTT state entirely."""
    a = ZerodhaAdapter.__new__(ZerodhaAdapter)
    a._paper = False
    a._log = MagicMock()
    a._paper_settle_gtt_triggers()                     # must be a no-op, not an error


# ── the limits, asserted so they cannot be quietly assumed away ───────────────

def test_LIMIT_a_paper_gtt_cannot_fire_while_nothing_reads_it():
    """⛔ THE LIMIT THAT MATTERS, PINNED. Zerodha evaluates server-side on every tick
    even while we are stopped -- that is the entire reason a CNC position is protected
    by a GTT overnight. This evaluates only when our own code asks.

    So a paper GTT CANNOT fire while the paper service is down, and the Mon->Tue carry
    stays irreducible. If this test ever has to change, someone has built an
    always-on paper evaluator, and the claim about what paper proves changes with it."""
    a = _paper_adapter(ltp=85.0)                        # price is well through the stop
    gid = _seed_gtt(a)
    assert a._paper_gtts[gid]["status"] == "active"     # nobody asked -> nothing fired


def test_LIMIT_trigger_does_not_fill_and_does_not_touch_the_holding():
    """⛔ Zerodha places a LIMIT leg on trigger, which may not fill. This places
    nothing, so the holding is left intact -- the F6 shape ('triggered but holding
    still > 0'), never the fill path. Reading a triggered paper GTT as 'position
    closed' would be the passes-wrongly failure this whole change is guarding."""
    a = _paper_adapter(ltp=85.0)
    a._paper_fills = {}
    gid = _seed_gtt(a)
    a.get_gtt(gid)
    assert a._paper_fills == {}                         # no order was placed


# ── B6: the proof the fix is real -- the same state, without cheating ─────────

def test_the_triggered_state_no_longer_needs_the_private_dict():
    """⭐⭐ THE STRONGEST EVIDENCE AVAILABLE THAT THIS FIX IS REAL.

    The established way to reach `triggered` in this suite is
        env.adapter._paper_gtts[str(gid)]["status"] = "triggered"
    -- reaching past the public API because the adapter could not produce the state.

    Here the identical observable is produced by moving the PRICE and reading the
    PUBLIC path. A test that no longer has to cheat is the difference between a
    simulation and a stub."""
    a = _paper_adapter(ltp=100.0)
    gid = _seed_gtt(a, sl=90.0, tgt=110.0)

    assert a.get_gtt(gid)["status"] == "active"         # no hand-written state anywhere
    a._quote_provider = lambda syms: {s: SimpleNamespace(last_price=88.0) for s in syms}

    rec = a.get_gtt(gid)
    assert rec["status"] == "triggered"
    assert rec["condition"]["last_price"] == 88.0
    # and the shape the Phase-2 reconcile parses is unchanged -- live and paper still
    # come back through the same dict contract.
    assert set(rec) == {"id", "status", "condition", "orders"}
    assert len(rec["orders"]) == 2
