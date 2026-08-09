"""tests/unit/test_tick2_pipeline_scoped_daily_gate.py — TICK 2.

⭐ TICK 2 IS NOT NEW POLICY. It implements the SECOND CLAUSE of Ruling 2 (Rama,
07-Aug): *"ONE simultaneous OPEN position per symbol, account-wide — and the symbol
becomes eligible again the INSTANT it is flat."* The second half has never been true,
because gate 1 is product-blind and DAY-scoped: after a delivery trade CLOSES it kept
blocking intraday on that symbol+direction for the rest of the day.

⛔ THE TWO RULES STAY SEPARATE — they are not merged:
  · Ruling 2 / gate 3 — one simultaneous OPEN position per symbol, ACCOUNT-WIDE.
    ⛔ UNCHANGED here. A delivery holding still blocks intraday WHILE IT IS OPEN.
  · Gate 1 — one completed trade per symbol+direction per day. ⭐ Becomes
    PER-PIPELINE: intraday's completed trade blocks intraday re-entry, delivery's
    blocks delivery, and neither blocks the other.

🔑 NO SCHEMA CHANGE. `trades` has no product column on `main` or on the sizing
branch (v46's eight new columns are all sizing-audit). The pipeline is derived from
`orders.product` via `LEFT JOIN … AND o.leg='ENTRY'` — the pattern already used in
five places in `state_store`.

⛔⛔ AND THE JOIN IS FAIL-CLOSED, WHICH IS THE WHOLE SAFETY ARGUMENT: a trade whose
product cannot be resolved (no ENTRY order row) counts for BOTH pipelines. That is
exactly today's behaviour, so an unresolvable row is byte-identical to the old gate
and there is no blind morning. The change is a strict RELAXATION only where the
product is KNOWN. A LEFT JOIN that let NULL fall out of both buckets would make a
protective gate fail OPEN, which is the wrong direction for a gate that exists to
stop the SENCO give-back.

⚠️ PARITY: ⭐ paper CAN exercise this one, and that is worth saying plainly — it is a
DB-PREDICATE gate, not a broker path. Both modes are tested and must resolve
identically; nothing here is a simulation.
"""
from __future__ import annotations

import pytest

from core.state_store import StateStore


# ═════════════════════════════════════════════════════════════════════════════
# fixtures — a real StateStore on a temp DB, so the SQL itself is under test
# ═════════════════════════════════════════════════════════════════════════════

TODAY = "2026-08-09"


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    try:
        yield s
    finally:
        try:
            s.close()
        except Exception:
            pass


def _trade(store, trade_id, symbol, direction, status="CLOSED", date=TODAY,
           mode="LIVE"):
    sig = "sig_" + trade_id
    store.execute(
        "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
        " triggered_at, received_at, expires_at, status, fingerprint, "
        " fingerprint_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sig, symbol, "scan", "strat", f"{date}T09:59:00+05:30",
         f"{date}T09:59:01+05:30", f"{date}T10:00:01+05:30", "PROCESSED",
         "fp_" + trade_id, date),
    )
    store.execute(
        "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
        " qty_planned, qty_filled, entry_target_price, sl_initial, tgt_initial, "
        " margin_reserved, risk_amount, created_at, updated_at, status, "
        " order_protocol, mode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, sig, symbol, direction, "strat", 1, 1, 100.0,
         99.0, 102.0, 20.0, 1.0, f"{date}T10:00:00+05:30",
         f"{date}T10:00:00+05:30", status, "LIMIT_TRIPLE", mode),
    )


def _entry_order(store, trade_id, product, leg_index=0):
    store.execute(
        "INSERT INTO orders (order_id, trade_id, leg, leg_index, "
        " transaction_type, order_type, product, variety, qty_requested, "
        " status, placed_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"o_{trade_id}_{leg_index}_{product}", trade_id, "ENTRY", leg_index,
         "BUY", "LIMIT", product, "regular", 1, "COMPLETE",
         f"{TODAY}T10:00:01+05:30", f"{TODAY}T10:00:01+05:30"),
    )


def _count(store, pipeline=None, symbol="SENCO", direction="LONG"):
    return store.count_executed_trades_today_for_symbol_direction(
        symbol, direction, TODAY, pipeline=pipeline)


# ═════════════════════════════════════════════════════════════════════════════
# THE DECOUPLING — the point of Tick 2
# ═════════════════════════════════════════════════════════════════════════════

def test_intraday_completed_blocks_intraday_and_ALLOWS_delivery(store):
    """⭐ THE decoupling test."""
    _trade(store, "t1", "SENCO", "LONG")
    _entry_order(store, "t1", "MIS")
    assert _count(store, "intraday") == 1, "intraday must still be blocked"
    assert _count(store, "delivery") == 0, "delivery must NOT be blocked by it"


def test_delivery_completed_blocks_delivery_and_ALLOWS_intraday(store):
    _trade(store, "t2", "SENCO", "LONG")
    _entry_order(store, "t2", "CNC")
    assert _count(store, "delivery") == 1
    assert _count(store, "intraday") == 0


def test_cover_orders_count_as_INTRADAY(store):
    """CO is an intraday product (resolver: COVER_ORDER -> 'CO')."""
    _trade(store, "t3", "SENCO", "LONG")
    _entry_order(store, "t3", "CO")
    assert _count(store, "intraday") == 1
    assert _count(store, "delivery") == 0


def test_delivery_is_a_CLOSED_set_and_intraday_is_its_COMPLEMENT(store):
    """⛔ An unknown-but-present product must NOT fall out of both buckets.
    Delivery is exactly {CNC}; everything else non-NULL is intraday. A new
    intraday-ish code can then never silently escape the gate."""
    _trade(store, "t4", "SENCO", "LONG")
    _entry_order(store, "t4", "BO")           # hypothetical future code
    assert _count(store, "intraday") == 1
    assert _count(store, "delivery") == 0


# ═════════════════════════════════════════════════════════════════════════════
# FAIL-CLOSED — the safety argument
# ═════════════════════════════════════════════════════════════════════════════

def test_an_unresolvable_product_blocks_BOTH_pipelines(store):
    """⛔⛔ No ENTRY order row => product unknown => counts for BOTH. This is
    today's behaviour preserved exactly, so an unresolvable row can never cause a
    wrong ALLOW and there is no blind morning."""
    _trade(store, "t5", "SENCO", "LONG")      # deliberately no ENTRY order
    assert _count(store, "intraday") == 1
    assert _count(store, "delivery") == 1


def test_pipeline_None_reproduces_the_OLD_gate_exactly(store):
    """⭐ The un-scoped call must stay byte-equivalent to the pre-Tick-2 gate, so
    every existing caller and test is unaffected."""
    _trade(store, "t6", "SENCO", "LONG")
    _entry_order(store, "t6", "MIS")
    _trade(store, "t7", "SENCO", "LONG")
    _entry_order(store, "t7", "CNC")
    assert _count(store, None) == 2


def test_multiple_ENTRY_legs_do_not_double_count(store):
    """⚠️ SCALE mode writes several ENTRY legs (leg_index 0/1/2). A naive
    LEFT JOIN + COUNT(*) would multiply the row and over-count the day."""
    _trade(store, "t8", "SENCO", "LONG")
    _entry_order(store, "t8", "MIS", leg_index=0)
    _entry_order(store, "t8", "MIS", leg_index=1)
    _entry_order(store, "t8", "MIS", leg_index=2)
    assert _count(store, "intraday") == 1, "one trade is one trade"
    assert _count(store, None) == 1


# ═════════════════════════════════════════════════════════════════════════════
# WHAT MUST NOT CHANGE
# ═════════════════════════════════════════════════════════════════════════════

def test_the_SENCO_shape_is_still_blocked(store):
    """⭐ Same pipeline, same symbol, same direction, same day — the give-back this
    gate was built for. ⛔ Tick 2 must not weaken it."""
    _trade(store, "t9", "SENCO", "LONG")
    _entry_order(store, "t9", "MIS")
    assert _count(store, "intraday") >= 1


def test_FAILED_and_REJECTED_attempts_still_do_not_consume_the_day(store):
    """FIX-181: an entry that never opened exposure must not spend the slot.
    ⛔ Reuses `_EXECUTED_TRADE_STATUSES`; a second definition is not introduced."""
    for i, st in enumerate(("FAILED", "CANCELLED", "REJECTED_RESERVE_FAILED")):
        _trade(store, f"tf{i}", "SENCO", "LONG", status=st)
        _entry_order(store, f"tf{i}", "MIS")
    assert _count(store, "intraday") == 0
    assert _count(store, None) == 0


def test_direction_still_scopes_the_gate(store):
    """A reversal is a different bet and must stay allowed."""
    _trade(store, "t10", "SENCO", "LONG")
    _entry_order(store, "t10", "MIS")
    assert _count(store, "intraday", direction="SHORT") == 0


def test_yesterdays_trade_does_not_block_today(store):
    _trade(store, "t11", "SENCO", "LONG", date="2026-08-08")
    _entry_order(store, "t11", "MIS")
    assert _count(store, "intraday") == 0


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_paper_and_live_resolve_IDENTICALLY(mode):
    """⭐ Paper CAN exercise this: it is a DB-predicate gate, not a broker path.
    ⛔ Nothing here is a simulation."""
    import tempfile, os
    d = tempfile.mkdtemp()
    s = StateStore(os.path.join(d, f"{mode}.db"))
    try:
        _trade(s, "tm", "SENCO", "LONG", mode=mode)
        _entry_order(s, "tm", "MIS")
        assert _count(s, "intraday") == 1
        assert _count(s, "delivery") == 0
    finally:
        try:
            s.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# THE GATE ITSELF — scoped, and the reject names the book
# ═════════════════════════════════════════════════════════════════════════════

def test_gate_passes_the_pipeline_and_names_it_in_the_reject():
    """⭐ Today a SYMBOL_DIRECTION_DAILY_LIMIT rejection cannot be attributed to a
    book. After Tick 2 it must be."""
    import inspect
    from signals.signal_processor import SignalProcessor
    src = inspect.getsource(SignalProcessor._enforce_one_trade_per_symbol_direction)
    assert "pipeline" in src, "the gate must resolve and pass a pipeline"
    i = src.index("SYMBOL_DIRECTION_DAILY_LIMIT")
    assert "pipeline" in src[i:], "the reject reason must name the book that blocked"


def test_gate_2_and_gate_3_are_untouched():
    """⛔ Gate 3 is what carries Ruling 2 and it is already correct."""
    import inspect
    from capital.risk_engine import RiskEngine
    src = inspect.getsource(RiskEngine)
    assert "_one_trade_per_symbol_direction" in src, "gate 1's flag still lives here"
    # the account-wide simultaneous-open rule must not have gained a pipeline scope
    for marker in ("pipeline=", "product ="):
        assert f"open_position{marker}" not in src


def test_the_gate_does_not_depend_on_the_processor_INSTANCE():
    """REGRESSION GUARD — this defect shipped twice in one day.

    The existing gate tests call `_enforce_one_trade_per_symbol_direction` on a
    minimal stub, so resolving the pipeline via `self._pipeline_for_intent(...)`
    raises AttributeError inside a LIVE ENTRY PATH. Phase 0's alert formatter had
    exactly this shape; the old tests caught it there and again here, because my
    new tests use a real object and cannot see it.
    """
    import inspect
    from signals.signal_processor import SignalProcessor
    src = inspect.getsource(SignalProcessor._enforce_one_trade_per_symbol_direction)
    assert "self._pipeline_for_intent" not in src, \
        "resolve the pipeline class-qualified, never off the instance"
    assert "SignalProcessor._pipeline_for_intent" in src


def test_the_gate_RUNS_on_a_bare_stub__the_carried_countermeasure():
    """§2 — THE PRACTICE THAT WAS RECORDED BUT NOT CARRIED.

    Phase 0 produced exactly this countermeasure (a case asserting the method
    works on a BARE STUB) after `self._alert_prefix(...)` shipped. It was not
    carried to Tick 2, and hours later `self._pipeline_for_intent(...)` shipped
    the identical defect. A practice that is recorded but not carried is not
    adopted.

    ⭐ This is BEHAVIOURAL, not source-inspection: a rename or a differently
    shaped instance lookup would slip past a grep and be caught here.
    """
    from signals.signal_processor import SignalProcessor

    class _Risk:
        _one_trade_per_symbol_direction = True

    class _Store:
        def __init__(self):
            self.calls = []

        def count_executed_trades_today_for_symbol_direction(self, *a, **kw):
            self.calls.append((a, kw))
            return 0                       # nothing traded => must not reject

    class _Stub:                            # the three attributes and nothing else
        _risk = _Risk()
        _store = _Store()

    stub = _Stub()
    # ⛔ must not raise AttributeError; must reach the store with a pipeline
    SignalProcessor._enforce_one_trade_per_symbol_direction(
        stub, "SENCO", "BUY", intent="DELIVERY")
    assert stub._store.calls, "the gate never reached the store"
    assert stub._store.calls[0][1].get("pipeline") == "delivery"
