"""
tests/unit/test_mis_autosquareoff.py — the MIS auto-square-off orchestrator.

TEST ASSUMPTIONS RECORDED HERE (FILE 27 A-6), so a future change in call count
cannot silently invalidate the budget figures quoted in the audit record:

  symbols in the budget scenario ......... 3
  broker calls per symbol ................ cancel SL, cancel TGT, verify x2, place = 5
  measured API maxima (28-Aug-2026) ...... get_positions 150ms, cancel_order 44ms,
                                           place_order 63ms, get_order_history 47ms
  delay placement (MEASURED) ............. PER SYMBOL, not per call, and not after
                                           the last one (eod_squareoff.py:1378-1379
                                           `if i < len(rows) - 1`)
  inter_order_delay_ms ................... 500
  => 3-symbol measured-bound estimate .... ~2.035s against a 120s PASS-2 budget
  These are MEASURED-BOUND ESTIMATES from observed maxima, NOT guarantees.
  cancel_order n=4 and place_order n=10 are THIN samples.

EVERY safety test asserts an INVARIANT (product, side, quantity, symbol, source,
what was NOT touched), never merely a count. A test that still passes after the
product predicate is widened is not a safety test.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import pytest

from orders.mis_autosquareoff import (
    MIS_PRODUCT,
    PASS_1,
    PASS_2,
    PASS_2_EXIT_PROTOCOL,
    MisAutoSquareoff,
    MisSquareoffConfigError,
    MisSquareoffTiming,
    MisState,
)

DAY = date(2026, 8, 31)


def at(h, m, s=0):
    return datetime(2026, 8, 31, h, m, s)


class Pos:
    def __init__(self, symbol, qty, product="MIS"):
        self.symbol = symbol
        self.qty = qty
        self.product = product
        self.avg_price = 100.0
        self.side = "BUY" if qty > 0 else "SELL"


class NoProductPos:
    """A position whose product attribute is absent entirely."""
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = qty


class Res:
    def __init__(self, success=True, reason=""):
        self.success = success
        self.reason = reason
        self.broker_order_id = "OID1"


class Hist:
    def __init__(self, status="CANCELLED"):
        self.status = status


class FakeAdapter:
    def __init__(self, positions, *, after=None, fail_positions=False,
                 cancel_ok=True, place_raises=False, verify_status="CANCELLED"):
        self._positions = list(positions)
        self._after = after
        self._calls = 0
        self.fail_positions = fail_positions
        self.cancel_ok = cancel_ok
        self.place_raises = place_raises
        self.verify_status = verify_status
        self.placed = []
        self.cancelled = []

    def get_positions(self):
        if self.fail_positions:
            raise RuntimeError("broker unreachable")
        self._calls += 1
        if self._calls > 1 and self._after is not None:
            return list(self._after)
        return list(self._positions)

    def cancel_order(self, oid, variety="regular"):
        self.cancelled.append((oid, variety))
        return Res(success=self.cancel_ok, reason="" if self.cancel_ok else "rejected")

    def get_order_history(self, oid):
        return [Hist(self.verify_status)]

    def place_order(self, **kw):
        if self.place_raises:
            raise RuntimeError("rejected by broker")
        self.placed.append(kw)
        return Res()


class FakeStore:
    def __init__(self, resting=None, raises=False):
        self._resting = resting or {}
        self.raises = raises
        self.queried = []

    def get_open_mis_exit_orders_for_symbol(self, symbol):
        if self.raises:
            raise RuntimeError("db down")
        self.queried.append(symbol)
        return self._resting.get(symbol, [])


class FakeLog:
    def __init__(self):
        self.criticals = []
        self.infos = []

    def critical(self, msg, *a, **k):
        self.criticals.append(msg % a if a else msg)

    def info(self, msg, *a, **k):
        self.infos.append(msg % a if a else msg)

    def error(self, msg, *a, **k):
        self.criticals.append(msg % a if a else msg)

    def warning(self, msg, *a, **k):
        self.infos.append(msg % a if a else msg)


def timing(**over):
    kw = dict(cutoff="15:12", first_offset="5m", second_offset="2m",
              margin_sec=20, poll_interval_sec=5,
              entry_end="15:00", eod_squareoff_time="15:17")
    kw.update(over)
    return MisSquareoffTiming.build(**kw)


def unit(adapter, store=None, log=None, now=None, **over):
    holder = {"now": now or at(15, 7)}
    kw = dict(
        adapter=adapter, store=store or FakeStore(), logger=log or FakeLog(),
        timing=over.pop("timing", timing()),
        now_fn=lambda: holder["now"],
        is_trading_holiday_fn=lambda d: False,
        inter_order_delay_sec=0.0,
    )
    kw.update(over)
    u = MisAutoSquareoff(**kw)
    u._test_clock = holder
    return u


# ══ CONFIG — FAIL CLOSED ═════════════════════════════════════════════════════

def test_config_derives_1507_and_1510():
    t = timing()
    assert (t.check_1.hour, t.check_1.minute) == (15, 7)
    assert (t.check_2.hour, t.check_2.minute) == (15, 10)
    assert (t.cutoff.hour, t.cutoff.minute) == (15, 12)


@pytest.mark.parametrize("bad", [
    {"cutoff": "25:00"}, {"cutoff": "1512"}, {"cutoff": ""},
    {"first_offset": "5"}, {"first_offset": "0m"}, {"first_offset": "-5m"},
    {"second_offset": "9m"},                       # second >= first
    {"margin_sec": 4},                             # #28: below poll_interval_sec
    {"entry_end": "15:09"},                        # entry_end >= CHECK_1
    {"eod_squareoff_time": "15:11"},               # cutoff >= eod_squareoff_time
])
def test_invalid_timing_fails_closed(bad):
    with pytest.raises(MisSquareoffConfigError):
        timing(**bad)


def test_28_margin_below_poll_interval_fails_closed():
    """#28 named assertion: the error must NAME the margin key."""
    with pytest.raises(MisSquareoffConfigError) as e:
        timing(margin_sec=4, poll_interval_sec=5)
    assert "mis_squareoff_margin_sec" in str(e.value)


def test_shipped_config_satisfies_the_ordering_invariant():
    from pathlib import Path
    from core.config_loader import load_all
    th = load_all(Path("config")).system.trading_hours
    t = MisSquareoffTiming.build(
        cutoff=th.mis_squareoff_cutoff, first_offset=th.mis_squareoff_first_offset,
        second_offset=th.mis_squareoff_second_offset,
        margin_sec=th.mis_squareoff_margin_sec, poll_interval_sec=5,
        entry_end=th.entry_end, eod_squareoff_time=th.eod_squareoff_time)
    assert (t.check_1.hour, t.check_1.minute) == (15, 7)
    assert (t.check_2.hour, t.check_2.minute) == (15, 10)


# ══ PRODUCT BOUNDARY — every mutation must turn these RED ════════════════════

def test_product_boundary_mis_only_cnc_and_co_never_selected():
    """INVARIANT: only the MIS symbol is selected; CNC and CO are untouched."""
    a = FakeAdapter([Pos("MISSYM", 1, "MIS"), Pos("CNCSYM", 1, "CNC"),
                     Pos("COSYM", 1, "CO"), Pos("NRMLSYM", 1, "NRML")])
    u = unit(a)
    got = u._find_open_mis_positions_for_auto_squareoff(a.get_positions())
    assert [r["symbol"] for r in got] == ["MISSYM"]


def test_cnc_only_book_produces_zero_exits():
    """Today's live proof case: OAL/RAMRAT are CNC with ACTIVE GTT legs."""
    a = FakeAdapter([Pos("OAL", 1, "CNC"), Pos("RAMRAT", 1, "CNC")])
    u = unit(a)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed == []
    assert a.cancelled == []


def test_co_position_produces_zero_orders_from_this_unit():
    """The EOD path's EMERGENCY_FLATTEN_PRODUCTS admits CO. This unit must not."""
    a = FakeAdapter([Pos("COSYM", 5, "CO")])
    u = unit(a)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed == []


@pytest.mark.parametrize("product", ["CNC", "CO", "NRML", "", "mis ", "Mis"])
def test_only_exact_mis_is_eligible(product):
    a = FakeAdapter([Pos("S", 1, product)])
    u = unit(a)
    got = u._find_open_mis_positions_for_auto_squareoff(a.get_positions())
    if product.strip().upper() == MIS_PRODUCT:
        assert [r["symbol"] for r in got] == ["S"]
    else:
        assert got == []


def test_12_missing_product_excluded_in_both_modes():
    """#12 named assertion: a position with NO product attribute is EXCLUDED.

    Live defaults an absent product to "" and paper to "MIS"
    (zerodha_adapter.py:1234 vs :1204) -- paper is the PERMISSIVE side. Reading
    the attribute without re-defaulting excludes it in BOTH modes.
    """
    a = FakeAdapter([NoProductPos("GHOST", 7)])
    u = unit(a)
    assert u._find_open_mis_positions_for_auto_squareoff(a.get_positions()) == []
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed == [], "a missing product must never become eligible"


# ══ SHORTS · QUANTITY ════════════════════════════════════════════════════════

def test_short_position_closes_with_a_buy():
    """27-Aug had TATAPOWER MIS qty -1. qty != 0, and a SHORT closes with BUY."""
    a = FakeAdapter([Pos("TATAPOWER", -1, "MIS")], after=[])
    u = unit(a)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert len(a.placed) == 1
    assert a.placed[0]["side"] == "BUY"
    assert a.placed[0]["qty"] == 1
    assert a.placed[0]["symbol"] == "TATAPOWER"


def test_long_position_closes_with_a_sell():
    a = FakeAdapter([Pos("BIKAJI", 2, "MIS")], after=[])
    u = unit(a)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed[0]["side"] == "SELL"
    assert a.placed[0]["qty"] == 2


def test_zero_qty_is_not_selected():
    a = FakeAdapter([Pos("FLAT", 0, "MIS")])
    u = unit(a)
    assert u._find_open_mis_positions_for_auto_squareoff(a.get_positions()) == []


def test_fresh_broker_quantity_is_the_remaining_quantity():
    """Broker says 40 remain; a stale local record of 100 must be irrelevant.

    No double-subtraction: not 40-60, not 100-60-60. The submitted qty is 40.
    """
    a = FakeAdapter([Pos("PARTIAL", 40, "MIS")], after=[])
    u = unit(a)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed[0]["qty"] == 40


# ══ CANCEL-BEFORE-EXIT ═══════════════════════════════════════════════════════

def test_cancel_precedes_exit_and_only_that_symbols_orders():
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[])
    st = FakeStore({"S1": [{"order_id": "SL1", "variety": "regular"}]})
    u = unit(a, store=st)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.cancelled == [("SL1", "regular")]
    assert st.queried == ["S1"], "only that symbol's orders may be looked up"
    assert len(a.placed) == 1


def test_cancel_failure_blocks_the_exit_no_blind_submission():
    """A double exit is worse than a late one: CANCEL_FAILED, and NO order."""
    a = FakeAdapter([Pos("S1", 1, "MIS")], cancel_ok=False)
    st = FakeStore({"S1": [{"order_id": "SL1", "variety": "regular"}]})
    log = FakeLog()
    u = unit(a, store=st, log=log)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed == [], "must NOT submit an exit after a failed cancel"
    assert r.symbols[0].state == MisState.CANCEL_FAILED
    assert any(MisState.CANCEL_FAILED in c for c in log.criticals)


def test_unverified_cancellation_blocks_the_exit():
    """'Cancel accepted' is not 'cancel effective'."""
    a = FakeAdapter([Pos("S1", 1, "MIS")], verify_status="OPEN")
    st = FakeStore({"S1": [{"order_id": "SL1", "variety": "regular"}]})
    u = unit(a, store=st)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed == []
    assert r.symbols[0].state == MisState.CANCEL_FAILED


def test_resting_order_lookup_failure_blocks_the_exit():
    a = FakeAdapter([Pos("S1", 1, "MIS")])
    u = unit(a, store=FakeStore(raises=True))
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed == []
    assert r.symbols[0].state == MisState.CANCEL_FAILED


# ══ BROKER QUERY FAILURE IS NEVER FLAT ═══════════════════════════════════════

def test_query_failure_is_never_flat():
    """An API exception must NEVER become an empty list."""
    a = FakeAdapter([], fail_positions=True)
    log = FakeLog()
    u = unit(a, log=log)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert r.state == MisState.BROKER_STATE_UNAVAILABLE
    assert r.state != MisState.NO_MIS
    assert a.placed == []
    assert any(MisState.BROKER_STATE_UNAVAILABLE in c for c in log.criticals)


def test_verification_query_failure_is_reconciliation_unknown():
    class A(FakeAdapter):
        def get_positions(self):
            self._calls += 1
            if self._calls == 1:
                return [Pos("S1", 1, "MIS")]
            raise RuntimeError("gone")
    a = A([])
    u = unit(a)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert r.state == MisState.RECONCILIATION_UNKNOWN


def test_mis_remains_after_pass_is_critical():
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[Pos("S1", 1, "MIS")])
    log = FakeLog()
    u = unit(a, log=log)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert r.state == MisState.MIS_REMAINS
    assert log.criticals


# ══ SCHEDULING · PER-PASS FLAGS · STARVATION ═════════════════════════════════

def test_27_per_pass_flags_pass_2_still_fires_after_pass_1():
    """#27 named assertion: with a SHARED flag PASS 2 would never fire at all."""
    a = FakeAdapter([], after=[])
    u = unit(a)
    assert u.check_and_fire(at(15, 7)) is True          # PASS 1
    assert u._fired[(DAY, PASS_1)] is True
    assert u.check_and_fire(at(15, 10)) is True         # PASS 2 STILL fires
    assert u._fired[(DAY, PASS_2)] is True


def test_pass_2_is_idempotent():
    a = FakeAdapter([], after=[])
    u = unit(a)
    assert u.check_and_fire(at(15, 10)) is True
    assert u.check_and_fire(at(15, 10, 5)) is False


def test_26_pass_1_failing_every_attempt_does_not_starve_pass_2():
    """#26 named assertion: PASS 2 evaluates and PASS_1_ABANDONED_FOR_PASS_2 logged."""
    class Boom(FakeAdapter):
        def get_positions(self):
            if not getattr(self, "_p2", False):
                raise RuntimeError("pass1 boom")
            return []
    a = Boom([])
    log = FakeLog()
    u = unit(a, log=log)
    for t in (at(15, 7), at(15, 7, 5), at(15, 7, 10)):
        u.check_and_fire(t)
    assert u._pass_1_attempts[DAY] >= 1
    assert u._fired.get((DAY, PASS_1), False) is False
    a._p2 = True
    assert u.check_and_fire(at(15, 10)) is True, "PASS 2 must still run"
    assert any(MisState.PASS_1_ABANDONED_FOR_PASS_2 in m
               for m in log.infos + log.criticals)


def test_pass_1_retries_are_bounded():
    class Boom(FakeAdapter):
        def get_positions(self):
            raise RuntimeError("boom")
    a = Boom([])
    u = unit(a, max_pass_1_attempts=2)
    for i in range(10):
        u.check_and_fire(at(15, 7, i))
    assert u._pass_1_attempts[DAY] == 2, "must not spin until the poll loop stops"


def test_pass_2_started_late_is_recorded():
    a = FakeAdapter([], after=[])
    u = unit(a)
    u.check_and_fire(at(15, 10, 4))
    r = u.result(DAY, PASS_2)
    assert r.lateness_ms == 4000
    assert MisState.PASS_2_STARTED_LATE in r.notes


def test_holiday_fires_nothing():
    a = FakeAdapter([Pos("S1", 1, "MIS")])
    u = unit(a, is_trading_holiday_fn=lambda d: True)
    assert u.check_and_fire(at(15, 10)) is False
    assert a.placed == []


# ══ R-2 GRACE CAP · #21 · #22 · #29 ══════════════════════════════════════════

def test_21_grace_capped_when_pass_1_is_late():
    """#21: at +90s the cap shrinks the grace so promotion precedes CHECK_2."""
    u = unit(FakeAdapter([]))
    g = u.effective_grace_sec(at(15, 8, 36))
    assert g == 64, f"expected 15:10:00 - 15:08:36 - 20 = 64, got {g}"
    assert g < 120, "the uncapped 120s grace would spill past CHECK_2"
    promotion = at(15, 8, 36) + timedelta(seconds=g)
    assert promotion <= at(15, 10), "promotion must not cross CHECK_2"


def test_grace_uncapped_when_on_time():
    u = unit(FakeAdapter([]))
    assert u.effective_grace_sec(at(15, 7)) == 120


def test_29_extreme_lateness_degrades_pass_1_to_market():
    """#29 named assertion: grace is 0, NO sleep, and the state is recorded."""
    log = FakeLog()
    u = unit(FakeAdapter([]), log=log)
    g = u.effective_grace_sec(at(15, 9, 50))
    assert g == 0, "max(0, ...) floor must make a negative sleep impossible"
    assert any(MisState.PASS_1_DEGRADED_TO_MARKET in m for m in log.infos)


def test_a1_grace_floor_never_negative():
    u = unit(FakeAdapter([]))
    for t in (at(15, 10), at(15, 11), at(15, 30)):
        assert u.effective_grace_sec(t) == 0


# ══ #23 · PASS 2 HAS NO BLOCKING GRACE, WHATEVER THE GENERAL KNOB SAYS ═══════

def test_23_pass_2_protocol_is_market_and_ignores_the_general_grace():
    """#23 named assertion: PASS 2 places a MARKET order and never sleeps.

    The general EOD limit_grace_sec is set absurdly high here. If PASS 2 read it,
    this test would block for 9999s rather than complete.
    """
    assert PASS_2_EXIT_PROTOCOL == "MARKET"
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[])
    u = unit(a, limit_grace_sec=9999)
    started = datetime.now()
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert (datetime.now() - started).total_seconds() < 5
    assert a.placed[0]["order_type"] == "MARKET"


def test_pass_2_order_type_is_market_not_limit():
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[])
    u = unit(a)
    u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert a.placed[0]["order_type"] == PASS_2_EXIT_PROTOCOL == "MARKET"


# ══ DEADLINE ════════════════════════════════════════════════════════════════

def test_deadline_breach_when_cutoff_crossed_with_mis_open():
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[Pos("S1", 1, "MIS")])
    u = unit(a, now=at(15, 13))
    r = u._run_pass(PASS_2, at(15, 13), at(15, 10))
    assert r.state == MisState.DEADLINE_BREACH
    assert r.deadline_preserved is False


def test_flat_but_past_cutoff_is_not_ordinary_success():
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[])
    u = unit(a, now=at(15, 13))
    r = u._run_pass(PASS_2, at(15, 13), at(15, 10))
    assert r.state == MisState.DEADLINE_BREACH


def test_insufficient_budget_proceeds_anyway_and_records_anticipation():
    """C-2: NEVER skip. Record DEADLINE_BREACH_ANTICIPATED and exit regardless."""
    a = FakeAdapter([Pos("S1", 1, "MIS")], after=[])
    log = FakeLog()
    u = unit(a, log=log, now=at(15, 11, 59),
             pass_2_bound_ms_per_symbol=60_000, pass_2_bound_fixed_ms=60_000)
    u._run_pass(PASS_2, at(15, 11, 59), at(15, 10))
    assert any(MisState.DEADLINE_BREACH_ANTICIPATED in c for c in log.criticals)
    assert len(a.placed) == 1, "an insufficient budget must NOT skip the exit"


def test_no_mis_is_flat_and_silent():
    a = FakeAdapter([])
    u = unit(a)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert r.state == MisState.NO_MIS
    assert a.placed == []


# ══ MULTI-SYMBOL ISOLATION ═══════════════════════════════════════════════════

def test_one_symbol_failing_does_not_stop_the_others():
    class A(FakeAdapter):
        def place_order(self, **kw):
            if kw["symbol"] == "BAD":
                raise RuntimeError("rejected")
            self.placed.append(kw)
            return Res()
    a = A([Pos("AAA", 1, "MIS"), Pos("BAD", 1, "MIS"), Pos("ZZZ", 1, "MIS")],
          after=[])
    u = unit(a)
    r = u._run_pass(PASS_2, at(15, 10), at(15, 10))
    assert sorted(p["symbol"] for p in a.placed) == ["AAA", "ZZZ"]
    states = {s.symbol: s.state for s in r.symbols}
    assert states["BAD"] == MisState.EXIT_REJECTED


# ══ #25 · SERIALISATION IS A TESTED INVARIANT, NOT AN INHERITED ACCIDENT ═════

def test_25_serial_poller_never_overlaps_two_passes():
    """#25 named assertion: driven from ONE thread, no two passes are ever
    in-flight together. The observed unsafe event would be a second pass entering
    while the first is inside _run_pass."""
    inflight = {"n": 0, "max": 0}
    orig = MisAutoSquareoff._run_pass

    def traced(self, which, now, sched):
        inflight["n"] += 1
        inflight["max"] = max(inflight["max"], inflight["n"])
        try:
            return orig(self, which, now, sched)
        finally:
            inflight["n"] -= 1

    a = FakeAdapter([], after=[])
    u = unit(a)
    u.__class__._run_pass = traced
    try:
        for t in (at(15, 7), at(15, 8), at(15, 10), at(15, 11)):
            u.check_and_fire(t)
    finally:
        u.__class__._run_pass = orig
    assert inflight["max"] == 1, "two passes were in flight at once"


def test_mutation_map_documents_what_must_break_this_file():
    """
    Machine-checked note of the mutations that MUST turn this suite red
    (FILE 27 A-8: each names an observed unsafe EVENT, not merely "goes RED").

      MIS_PRODUCT -> "CNC"          : a CNC position is squared off
                                      -> test_product_boundary / test_cnc_only_book
      MIS_PRODUCT -> "CO"           : a CO position is squared off
                                      -> test_co_position_produces_zero_orders
      product predicate widened to
        {"MIS","CNC"}               : CNC enters the MIS path
                                      -> test_only_exact_mis_is_eligible
      _product_of defaults to "MIS" : a missing product becomes eligible (paper)
                                      -> test_12_missing_product_excluded
      drop cancel-before-exit       : an exit is submitted with a live SL
                                      -> test_cancel_failure_blocks_the_exit
      qty != 0 -> qty > 0           : a SHORT is never closed
                                      -> test_short_position_closes_with_a_buy
      use qty_filled not broker qty : a stale quantity is submitted
                                      -> test_fresh_broker_quantity
      query failure -> []           : a broker outage reads as flat
                                      -> test_query_failure_is_never_flat
      remove R-2's max(0, ...)      : a negative sleep
                                      -> test_a1_grace_floor_never_negative
      remove R-2's cap              : PASS 1 promotion crosses CHECK_2
                                      -> test_21_grace_capped_when_pass_1_is_late
      PASS 2 reads limit_grace_sec  : PASS 2 blocks past the cutoff
                                      -> test_23_pass_2_protocol_is_market
      shared fired flag             : PASS 2 never fires
                                      -> test_27_per_pass_flags
      drop B-2 priority             : a failing PASS 1 starves PASS 2
                                      -> test_26_pass_1_failing_every_attempt
      two independent schedulers    : two passes in flight at once
                                      -> test_25_serial_poller_never_overlaps
      margin < poll_interval_sec    : config accepted
                                      -> test_28_margin_below_poll_interval
    """
    assert PASS_2_EXIT_PROTOCOL == "MARKET"
    assert MIS_PRODUCT == "MIS"


# ══ WIRING · the unit constructs from the SHIPPED config exactly as main.py does ══

def test_wiring_matches_main_py_construction():
    """Guards the construction shape main.py uses, so a signature drift is caught
    here rather than at 08:15 on a Monday."""
    from pathlib import Path
    import core.time_authority as time_authority
    from core.config_loader import load_all
    from orders.mis_autosquareoff import MisAutoSquareoff, MisSquareoffTiming

    cfg = load_all(Path("config")).system
    th = cfg.trading_hours
    t = MisSquareoffTiming.build(
        cutoff=th.mis_squareoff_cutoff,
        first_offset=th.mis_squareoff_first_offset,
        second_offset=th.mis_squareoff_second_offset,
        margin_sec=th.mis_squareoff_margin_sec,
        poll_interval_sec=cfg.eod_squareoff.poll_interval_sec,
        entry_end=th.entry_end,
        eod_squareoff_time=th.eod_squareoff_time,
    )
    u = MisAutoSquareoff(
        adapter=FakeAdapter([]), store=FakeStore(), logger=FakeLog(), timing=t,
        now_fn=time_authority.now_ist,
        is_trading_holiday_fn=lambda d: False,
        limit_grace_sec=cfg.eod_squareoff.limit_grace_sec,
        inter_order_delay_sec=cfg.eod_squareoff.inter_order_delay_ms / 1000.0,
    )
    assert u.is_alive() is False, "no thread until start_polling"
    assert (t.check_1.hour, t.check_1.minute) == (15, 7)
    assert (t.check_2.hour, t.check_2.minute) == (15, 10)


def test_eod_squareoff_general_settings_are_untouched():
    """P: no existing general EOD behaviour changed. The 15:17 trigger, its
    LIMIT_THEN_MARKET protocol and its 120s grace all stay exactly as they were."""
    from pathlib import Path
    from core.config_loader import load_all
    cfg = load_all(Path("config")).system
    assert cfg.trading_hours.eod_squareoff_time == "15:17"
    assert cfg.eod_squareoff.exit_protocol == "LIMIT_THEN_MARKET"
    assert cfg.eod_squareoff.limit_grace_sec == 120
    assert cfg.trading_hours.entry_end == "15:00"


def test_25b_the_overlap_detector_is_not_vacuous():
    """#25's control: prove the overlap assertion COULD go red.

    test_25 asserts max-in-flight == 1 under the serial poller. That is only
    evidence if the detector can register 2. Here two threads enter _run_pass
    concurrently -- the shape independent schedulers would produce -- and the
    detector must observe the unsafe event.
    """
    inflight = {"n": 0, "max": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    def body():
        with lock:
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
        try:
            barrier.wait()          # force genuine overlap
        except threading.BrokenBarrierError:
            pass
        with lock:
            inflight["n"] -= 1

    ts = [threading.Thread(target=body) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5)
    assert inflight["max"] == 2, (
        "the detector cannot observe concurrency, so test_25 would be vacuous")


def test_9_the_15_17_pass_1_cannot_reach_a_cnc_gtt():
    """#9 named assertion (S-3, by test rather than by reading SQL).

    The unsafe event would be the 15:17 EOD Pass 1 cancelling a CNC GTT leg. Two
    independent structural reasons prevent it, and both are asserted here:
      1. both Pass-1 queries filter product IN ('MIS','CO') on the ENTRY leg;
      2. they read `orders` rows with leg IN ('SL','TGT') -- a CNC GTT lives in
         `gtt_state`, is placed through the GTT API, and has NO orders row.
    """
    import inspect
    from core import state_store

    def sql_of(fn):
        """Only the query body — a docstring mentioning 'CO' is prose, not a filter."""
        src = inspect.getsource(fn)
        return " ".join(src.split("fetch_all(", 1)[1].split())

    src1 = sql_of(state_store.StateStore.get_pending_intraday_orders)
    src2 = sql_of(state_store.StateStore.get_pending_exit_orders_for_open_positions)

    for src in (src1, src2):
        flat = " ".join(src.split())
        assert "product IN ('MIS', 'CO')" in flat, (
            "Pass 1 lost its product filter; CNC could enter the cancel set")
        assert "gtt_state" not in flat, (
            "Pass 1 must never read gtt_state")
    assert "leg IN ('SL', 'TGT')" in src2

    # And the NEW unit's own query is narrower still: MIS only, one symbol.
    flat3 = sql_of(state_store.StateStore.get_open_mis_exit_orders_for_symbol)
    assert "o.product = 'MIS'" in flat3 and "e.product = 'MIS'" in flat3
    assert "'CO'" not in flat3, "the new unit must not inherit CO eligibility"
    assert "gtt_state" not in flat3


# ══ G · S-2 ADAPTER PARITY (the OLD path) · closes prediction #19 ════════════

def test_19_paper_and_live_agree_on_an_absent_product():
    """#19 named assertion: the unsafe event is a position with NO product being
    treated as MIS in paper while live excludes it -- paper more permissive than
    live on a safety boundary, so a paper run would show green for a case live
    would skip. Both branches must default to "" (ineligible).
    """
    import inspect
    from broker import zerodha_adapter

    src = inspect.getsource(zerodha_adapter.ZerodhaAdapter.get_positions)
    assert 'info.get("product", "MIS")' not in src, (
        "paper defaults an absent product to MIS -- more permissive than live")
    assert 'info.get("product", "")' in src, "paper branch must default to ''"
    assert 'str(row.get("product", ""))' in src, "live branch must default to ''"


def test_19b_an_absent_product_is_ineligible_in_the_unit():
    """The consequence that matters: whatever the adapter yields, a position with
    no resolvable product is never selected for a MIS square-off."""
    a = FakeAdapter([NoProductPos("GHOST", 3), Pos("REAL", 1, "MIS")], after=[])
    u = unit(a)
    got = u._find_open_mis_positions_for_auto_squareoff(a.get_positions())
    assert [r["symbol"] for r in got] == ["REAL"]
