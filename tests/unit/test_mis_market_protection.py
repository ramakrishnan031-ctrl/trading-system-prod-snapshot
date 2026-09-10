"""Block A — protected MARKET on the MIS auto-squareoff, and the PASS_2 residual gate.

Built 10-Sep-2026 against `3b15bbf`. Two independent properties are under test and
they must stay independent:

  * the PROTECTION BAND actually reaches the wire, as a PERCENT; and
  * PASS_2 never coexists with an unresolved PASS_1 order.

⚠️⚠️ THE UNITS TEST IS THE ONE THAT MATTERS MOST.
`eod_squareoff.limit_aggressive_pct` is a FRACTION (0.01 == 1%). `market_protection`
is a PERCENT (1.5 == 1.5%). Following the neighbouring convention by reflex sends
0.015 — i.e. 0.015%, a band ~100x too tight that would essentially never fill while
looking like a working fix. `test_wire_value_is_1_point_5_not_0_015` asserts the
exact bytes and is the single assertion that catches that whole class of error.

⛔ NO REAL PARTIAL FILL IS MANUFACTURED ANYWHERE. A protection-converted partial
needs price to move outside the band DURING execution, which cannot be commanded on
the twin or anywhere else. Partial-fill detection, PASS_2-blocked-by-partial and the
residual arithmetic are unit-only, permanently. The `after=` hook on the existing
FakeAdapter models the *position consequence* of a partial; it does not produce one.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402  (used only to form-encode, exactly as kiteconnect does)

from orders.mis_autosquareoff import (  # noqa: E402
    PASS_1,
    PASS_2,
    MisAutoSquareoff,
    MisSquareoffTiming,
    MisState,
)

# Reuse the EXISTING doubles rather than forking a second harness (§5.11).
from tests.unit.test_mis_autosquareoff import (  # noqa: E402
    FakeAdapter,
    FakeLog,
    FakeStore,
    Pos,
    Res,
    at,
    timing,
)


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def unit(adapter, store=None, log=None, now=None, **over):
    """Local copy of the sibling module's builder, plus the two new bands."""
    holder = {"now": now or at(15, 7)}
    kw: dict = dict(
        adapter=adapter,
        store=store or FakeStore(),
        logger=log or FakeLog(),
        timing=over.pop("timing", timing()),
        now_fn=lambda: holder["now"],
        is_trading_holiday_fn=lambda d: False,
        inter_order_delay_sec=0.0,
    )
    kw.update(over)
    u = MisAutoSquareoff(**kw)
    u._test_clock = holder
    return u


def seed_pass_1(u, symbol: str, broker_order_id: str) -> None:
    """Put a PASS_1 result in place exactly as a real PASS_1 would have.

    Deliberately goes through the unit's own `_store_result`, so the test exercises
    the real retention path (`:640` -> `_results`) rather than a parallel fiction.
    """
    from orders.mis_autosquareoff import PassResult, SymbolOutcome

    res = PassResult(which=PASS_1, state=MisState.MIS_FOUND)
    res.symbols.append(
        SymbolOutcome(
            symbol=symbol,
            state=MisState.EXIT_SUBMITTED,
            broker_order_id=broker_order_id,
            exit_side="SELL",
            requested_qty=100,
            broker_qty_at_start=100,
        )
    )
    u._store_result(u._now().date(), PASS_1, res)


class OpenOrder:
    """Shape returned by adapter.get_open_orders(): a plain dict in production."""

    def __init__(self, order_id, symbol="X", trigger_price=0.0):
        self.order_id = order_id
        self.symbol = symbol
        self.trigger_price = trigger_price


# ═════════════════════════════════════════════════════════════════════════════
# §5.1 / §5.2 — THE ADAPTER, AND THE WIRE
# ═════════════════════════════════════════════════════════════════════════════

class RecordingKite:
    """Records the kwargs the adapter hands to kiteconnect."""

    def __init__(self):
        self.calls: list[dict] = []

    def place_order(self, **kw: Any) -> str:
        self.calls.append(dict(kw))
        return "KITE_REC_1"

    # unused by these tests, present so the adapter constructs
    def cancel_order(self, **kw: Any) -> None: ...
    def modify_order(self, **kw: Any) -> None: ...


def _adapter_with(kite):
    from tests.unit.test_zerodha_adapter import _make_adapter

    adapter, _kite, _rl, _osm, _log = _make_adapter(kite=kite)
    return adapter


def _kite_kwargs_for(market_protection):
    kite = RecordingKite()
    adapter = _adapter_with(kite)
    adapter.place_order(
        symbol="RELIANCE", side="SELL", qty=10, price=0.0,
        order_type="MARKET", intent="INTRADAY",
        market_protection=market_protection,
    )
    assert len(kite.calls) == 1
    return kite.calls[0]


def test_adapter_passes_market_protection_through_to_kite():
    """§5.1a — the value reaches the single kite.place_order chokepoint."""
    kw = _kite_kwargs_for(1.5)
    assert kw["market_protection"] == 1.5


def test_adapter_none_omits_market_protection_entirely():
    """§5.1b — None must reproduce today's wire body byte-for-byte.

    The adapter passes market_protection=None explicitly; kiteconnect 5.1.0 then
    deletes it in `params = locals()`. Assert BOTH halves: the adapter passes None,
    and None does not survive form-encoding.
    """
    kw = _kite_kwargs_for(None)
    assert kw["market_protection"] is None
    body = _encode_like_kiteconnect(kw)
    assert "market_protection" not in body


def _encode_like_kiteconnect(kite_kwargs: dict) -> str:
    """Replicate kiteconnect 5.1.0 place_order EXACTLY, then form-encode.

    `params = locals()` -> delete every `is None` entry -> `requests` POST with
    `data=params` (is_json is False for order.place). This is the wire body.
    """
    params = {k: v for k, v in kite_kwargs.items() if v is not None}
    prepared = requests.Request(
        "POST", "https://api.kite.trade/orders/regular", data=params
    ).prepare()
    return prepared.body or ""


def test_wire_value_is_1_point_5_not_0_015():
    """⭐⭐ §5.2 — THE UNITS ASSERTION. This is the one that catches the class.

    A fraction-style 0.015 written for "1.5%" would silently become a 0.015% band.
    Assert the exact bytes on the wire, and assert the fraction form is absent.
    """
    body = _encode_like_kiteconnect(_kite_kwargs_for(1.5))
    assert "market_protection=1.5" in body
    assert "market_protection=0.015" not in body
    assert "0.015" not in body


def test_wire_value_for_pass_2_is_2_point_5():
    """§5.2 — the PASS_2 band, same assertion shape."""
    body = _encode_like_kiteconnect(_kite_kwargs_for(2.5))
    assert "market_protection=2.5" in body
    assert "market_protection=0.025" not in body


@pytest.mark.parametrize("bad", [0.015, 0.0, 0, -1, -7, 999, 100, 10.01, 0.09])
def test_adapter_rejects_out_of_range_market_protection(bad):
    """§5.1c — the SDK validates NOTHING, so we must. 0.015 is the units error.

    `-1` is refused deliberately: Zerodha accepts it as 'automatic', but this
    repair ships FIXED, auditable values, so -1 is out of contract here.
    """
    kite = RecordingKite()
    adapter = _adapter_with(kite)
    with pytest.raises(ValueError, match="market_protection"):
        adapter.place_order(
            symbol="RELIANCE", side="SELL", qty=10, price=0.0,
            order_type="MARKET", intent="INTRADAY", market_protection=bad,
        )
    assert kite.calls == [], "rejected order must never reach the broker"


@pytest.mark.parametrize("ok", [0.1, 1.5, 2.5, 10.0])
def test_adapter_accepts_in_range_market_protection(ok):
    """§5.1c — the bounds are inclusive at both ends."""
    assert _kite_kwargs_for(ok)["market_protection"] == ok


def test_out_of_range_is_rejected_in_paper_mode_too():
    """PARITY (rules 5/8): validation runs BEFORE the `if self._paper` branch, so a
    bad band fails identically in paper and live. The value itself has no meaning
    for a synthesized paper fill — the REJECTION is what must match."""
    from tests.unit.test_zerodha_adapter import _make_adapter

    adapter, _k, _rl, _osm, _lg = _make_adapter(paper=True)
    with pytest.raises(ValueError, match="market_protection"):
        adapter.place_order(
            symbol="RELIANCE", side="SELL", qty=10, price=0.0,
            order_type="MARKET", intent="INTRADAY", market_protection=0.015,
        )


@pytest.mark.parametrize(
    "order_type,price,trigger",
    [("LIMIT", 100.0, 0.0), ("MARKET", 0.0, 0.0), ("SL", 100.0, 101.0)],
)
def test_existing_callers_are_byte_identical(order_type, price, trigger):
    """§8.5 — ENTRY / SL / TGT / EOD never pass the new parameter, so their wire
    body must be exactly what it was before this change: no market_protection key."""
    kite = RecordingKite()
    adapter = _adapter_with(kite)
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=price,
        order_type=order_type, intent="INTRADAY", trigger_price=trigger,
    )
    kw = kite.calls[0]
    assert kw["market_protection"] is None
    assert "market_protection" not in _encode_like_kiteconnect(kw)


# ═════════════════════════════════════════════════════════════════════════════
# §5.2 — THE SQUAREOFF UNIT SENDS THE RIGHT BAND PER PASS
# ═════════════════════════════════════════════════════════════════════════════

def test_pass_1_submits_1_point_5_and_pass_2_submits_2_point_5():
    """§5.2 — the exact numbers, and the asymmetry, end to end through the unit."""
    a1 = FakeAdapter([Pos("SYMA", 5, "MIS")])
    u1 = unit(a1)
    u1._run_pass(PASS_1, at(15, 3), at(15, 3))
    assert a1.placed[0]["market_protection"] == 1.5

    a2 = FakeAdapter([Pos("SYMA", 5, "MIS")])
    u2 = unit(a2)
    u2._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert a2.placed[0]["market_protection"] == 2.5


def test_bands_come_from_config_not_hardcoded():
    """§2.1 — overriding the constructor changes what is submitted."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")])
    u = unit(a, pass_1_market_protection_percent=3.0,
             pass_2_market_protection_percent=4.0)
    u._run_pass(PASS_1, at(15, 3), at(15, 3))
    assert a.placed[0]["market_protection"] == 3.0


def test_submitted_band_is_recorded_in_the_audit_record():
    """§2.2 — the system must be able to prove which band it SUBMITTED."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")])
    u = unit(a)
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert [o.market_protection for o in r.symbols] == [2.5]


# ═════════════════════════════════════════════════════════════════════════════
# §5.3 – §5.10 — THE PASS_2 RESIDUAL GATE
# ═════════════════════════════════════════════════════════════════════════════

def test_pass_1_broker_order_id_is_retained_and_read():
    """§5.3 — retention already existed; this proves PASS_2 now CONSULTS it."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")])
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    assert u._pass_1_order_id_for(u._now().date(), "SYMA") == "P1_ORDER_9"

    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert r.symbols[0].pass_1_order_id == "P1_ORDER_9"


def test_pass_2_not_placed_when_position_is_flat():
    """§5.4 / §3.1 — flat means no candidate, so no PASS_2 order for that symbol."""
    a = FakeAdapter([])
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert a.placed == []
    assert r.state == MisState.NO_MIS


def test_residual_cancel_targets_the_exact_pass_1_order_id():
    """§5.5 — the residual is cancelled BY ID, not by a symbol-wide sweep."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")], verify_status="CANCELLED")
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert ("P1_ORDER_9", "regular") in a.cancelled


def test_pass_2_proceeds_when_residual_cancellation_confirmed():
    """§5.6 — resolved residual -> the PASS_2 exit is submitted, with its band."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")], verify_status="CANCELLED")
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert len(a.placed) == 1
    assert a.placed[0]["market_protection"] == 2.5
    assert r.symbols[0].state == MisState.EXIT_SUBMITTED
    assert "CONFIRMED" in r.symbols[0].residual_detail


def test_residual_absent_from_broker_is_resolved_without_a_cancel():
    """A PASS_1 order already terminal at the broker needs no cancel call."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")])
    a.open_orders = []                       # not live any more
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert ("P1_ORDER_9", "regular") not in a.cancelled
    assert "already terminal" in r.symbols[0].residual_detail
    assert len(a.placed) == 1


def test_pass_2_blocked_when_cancellation_not_confirmed_others_unaffected():
    """§5.7 — the core safety property, plus per-symbol isolation.

    SYMA's residual refuses to go terminal (`verify_status='OPEN'`), so SYMA must
    be blocked, CRITICAL, and NOT placed. SYMB has no PASS_1 order and must be
    completely unaffected.
    """
    log = FakeLog()
    a = FakeAdapter(
        [Pos("SYMA", 5, "MIS"), Pos("SYMB", 7, "MIS")], verify_status="OPEN"
    )
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a, log=log)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")

    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))

    by_symbol = {o.symbol: o for o in r.symbols}
    assert by_symbol["SYMA"].state == MisState.CANCEL_FAILED
    assert "NOT CONFIRMED" in by_symbol["SYMA"].detail
    placed_symbols = [p["symbol"] for p in a.placed]
    assert "SYMA" not in placed_symbols, "blocked symbol must not be placed"
    assert "SYMB" in placed_symbols, "an unrelated symbol must still exit"
    assert any("SYMA" in c for c in log.criticals)


def test_pass_2_blocked_when_open_order_read_fails():
    """An unknown is not a resolution: a failed broker read must BLOCK, not place."""
    class Boom(FakeAdapter):
        def get_open_orders(self):
            raise RuntimeError("broker unreachable")

    a = Boom([Pos("SYMA", 5, "MIS")])
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert a.placed == []
    assert r.symbols[0].state == MisState.CANCEL_FAILED
    assert "read failed" in r.symbols[0].detail


def test_blocked_symbol_does_not_cancel_its_protective_legs():
    """The gate runs BEFORE the protective legs are touched, so a BLOCK leaves
    whatever protection exists standing. That is why no restore is needed here."""
    a = FakeAdapter([Pos("SYMA", 5, "MIS")], verify_status="OPEN")
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    store = FakeStore(resting={"SYMA": [{"order_id": "SL_1", "leg": "SL",
                                         "variety": "regular", "trade_id": "T1"}]})
    u = unit(a, store=store)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert ("SL_1", "regular") not in a.cancelled, \
        "protective leg must be untouched when PASS_2 is blocked"


def test_fresh_broker_quantity_determines_pass_2_quantity():
    """§5.8 — THE 60/40 CASE, and the reason a correct quantity is not sufficient.

    PASS_1 saw 100. By PASS_2 the broker reports 40 (the position consequence of a
    partial). PASS_2 must size from the FRESH read, not from the stale one.
    ⛔ No real partial fill is produced — `after=` models the consequence only.
    """
    a = FakeAdapter(
        [Pos("SYMA", 100, "MIS")],
        after=[Pos("SYMA", 40, "MIS")],
        verify_status="CANCELLED",
    )
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert len(a.placed) == 1
    assert a.placed[0]["qty"] == 40, "PASS_2 must size from the fresh position"


def test_flat_on_post_residual_reread_places_nothing():
    """If the residual FILLED, the re-read shows flat and PASS_2 must not place.

    This is precisely why the re-read exists: `_verify_cancelled` treats COMPLETE
    as terminal, so 'confirmed' does NOT imply 'position unchanged'.
    """
    a = FakeAdapter(
        [Pos("SYMA", 100, "MIS")],
        after=[],                       # filled -> position gone
        verify_status="COMPLETE",
    )
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert a.placed == [], "must not place on top of a filled residual"
    assert r.symbols[0].state == MisState.EXIT_FILLED
    assert r.symbols[0].requested_qty == 0


def test_position_reread_failure_is_not_flat():
    """§5.9 / §3.10 — a query failure is RECONCILIATION_UNKNOWN, never FLAT, and
    it is not a sizing basis either, so nothing is placed."""
    class FailsSecondRead(FakeAdapter):
        def __init__(self, *a_, **k_):
            super().__init__(*a_, **k_)
            self._pos_calls = 0

        def get_positions(self):
            self._pos_calls += 1
            if self._pos_calls >= 2:
                raise RuntimeError("broker unreachable")
            return list(self._positions)

    a = FailsSecondRead([Pos("SYMA", 5, "MIS")], verify_status="CANCELLED")
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert a.placed == []
    assert r.symbols[0].state == MisState.RECONCILIATION_UNKNOWN
    assert r.state != MisState.NO_MIS, "a failed query must never read as flat"


def test_no_third_pass_exists():
    """§5.10 / §3.9 — PASS_2 is the last word. `_retry_remaining_mis_positions`
    stays dead: it has no call site anywhere in the repo and is not an approved
    mechanism. Asserted structurally so a future call site fails this test."""
    import inspect

    import orders.mis_autosquareoff as mod

    src = inspect.getsource(mod)
    assert src.count("_retry_remaining_mis_positions") == 1, (
        "only the def may mention it — a second occurrence means a call site "
        "appeared and a third pass has been wired"
    )
    root = Path(__file__).resolve().parents[2]
    callers = []
    for py in root.rglob("*.py"):
        parts = py.parts
        if "venv" in parts or "tests" in parts:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "_retry_remaining_mis_positions(" in text and py.name != "mis_autosquareoff.py":
            callers.append(str(py))
    assert callers == [], f"a third pass was wired from: {callers}"


def test_pass_1_never_pays_for_the_residual_gate():
    """PASS_1 has no predecessor, so it must make zero extra broker calls."""
    class CountingOpenOrders(FakeAdapter):
        def __init__(self, *a_, **k_):
            super().__init__(*a_, **k_)
            self.open_order_reads = 0

        def get_open_orders(self):
            self.open_order_reads += 1
            return list(self.open_orders)

    a = CountingOpenOrders([Pos("SYMA", 5, "MIS")])
    u = unit(a)
    u._run_pass(PASS_1, at(15, 3), at(15, 3))
    assert a.open_order_reads == 0


def test_pass_2_without_a_pass_1_order_makes_no_extra_broker_call():
    """When PASS_1 placed nothing there is no residual by construction, so the
    gate must not spend a broker read. This is what keeps the pre-existing
    PASS_2-only tests behaviourally identical."""
    class CountingOpenOrders(FakeAdapter):
        def __init__(self, *a_, **k_):
            super().__init__(*a_, **k_)
            self.open_order_reads = 0

        def get_open_orders(self):
            self.open_order_reads += 1
            return list(self.open_orders)

    a = CountingOpenOrders([Pos("SYMA", 5, "MIS")])
    u = unit(a)                       # no seed_pass_1
    u._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert a.open_order_reads == 0
    assert len(a.placed) == 1


# ═════════════════════════════════════════════════════════════════════════════
# §3 (10-Sep, review) — TWO OUTCOMES THAT MUST NEVER COLLAPSE INTO ONE
# ═════════════════════════════════════════════════════════════════════════════

def test_query_failure_and_id_absent_are_distinct_outcomes():
    """§3.1 — the decisive pair, asserted side by side in ONE test.

    query SUCCEEDED + exact id absent -> no live residual -> PASS_2 PROCEEDS
    query FAILED / None / malformed   -> unknown          -> PASS_2 BLOCKED

    ⚠️ This test found a real defect on the way in. The first revision of
    `_read_open_order_ids` wrote `get_open_orders() or []`, so a None response
    became an EMPTY set -- i.e. "absent" -- i.e. "resolved". A degraded broker
    read would have silently authorised PASS_2 to place on top of a live order.
    """
    # (a) query SUCCEEDED, id genuinely absent -> proceed
    ok = FakeAdapter([Pos("SYMA", 5, "MIS")])
    ok.open_orders = []
    u_ok = unit(ok)
    seed_pass_1(u_ok, "SYMA", "P1_ORDER_9")
    r_ok = u_ok._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert len(ok.placed) == 1, "a clean 'absent' must not block"
    assert r_ok.symbols[0].state == MisState.EXIT_SUBMITTED

    # (b) query returned None -> must NOT read as absent
    class ReturnsNone(FakeAdapter):
        def get_open_orders(self):
            return None

    none_a = ReturnsNone([Pos("SYMA", 5, "MIS")])
    u_none = unit(none_a)
    seed_pass_1(u_none, "SYMA", "P1_ORDER_9")
    r_none = u_none._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert none_a.placed == [], "None must never collapse into 'order absent'"
    assert r_none.symbols[0].state == MisState.CANCEL_FAILED

    # (c) query raised -> must NOT read as absent
    class Raises(FakeAdapter):
        def get_open_orders(self):
            raise RuntimeError("broker unreachable")

    raise_a = Raises([Pos("SYMA", 5, "MIS")])
    u_raise = unit(raise_a)
    seed_pass_1(u_raise, "SYMA", "P1_ORDER_9")
    r_raise = u_raise._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert raise_a.placed == [], "an exception must never collapse into 'absent'"
    assert r_raise.symbols[0].state == MisState.CANCEL_FAILED

    # (d) malformed rows -> a failure, not an exception thrown through the pass
    class Malformed(FakeAdapter):
        def get_open_orders(self):
            return 12345          # not iterable

    bad = Malformed([Pos("SYMA", 5, "MIS")])
    u_bad = unit(bad)
    seed_pass_1(u_bad, "SYMA", "P1_ORDER_9")
    r_bad = u_bad._run_pass(PASS_2, at(15, 6), at(15, 6))
    assert bad.placed == [], "a malformed response must block, not place"
    assert r_bad.symbols[0].state == MisState.CANCEL_FAILED


def test_pass_1_observed_complete_cannot_oversell_the_filled_amount():
    """§3.2 — the fresh position re-read is AUTHORITATIVE over the stale one.

    The exact PASS_1 order is observed COMPLETE (not cancelled). `_verify_cancelled`
    treats COMPLETE as terminal, so the residual reads as 'resolved' -- while the
    quantity has moved underneath. PASS_2 must place the REMAINING 40, never the
    original 100.

    ⛔ NO REAL PARTIAL FILL IS PRODUCED. `after=` models the POSITION CONSEQUENCE
    of one. A protection-converted partial cannot be commanded here or on the twin.
    """
    a = FakeAdapter(
        [Pos("SYMA", 100, "MIS")],
        after=[Pos("SYMA", 40, "MIS")],
        verify_status="COMPLETE",          # the PASS_1 order FILLED, not cancelled
    )
    a.open_orders = [OpenOrder("P1_ORDER_9", "SYMA")]
    u = unit(a)
    seed_pass_1(u, "SYMA", "P1_ORDER_9")
    r = u._run_pass(PASS_2, at(15, 6), at(15, 6))

    assert len(a.placed) == 1
    assert a.placed[0]["qty"] == 40, "PASS_2 oversold the amount PASS_1 already filled"
    assert a.placed[0]["qty"] != 100
    assert r.symbols[0].broker_qty_at_start == 40
    assert r.symbols[0].market_protection == 2.5
