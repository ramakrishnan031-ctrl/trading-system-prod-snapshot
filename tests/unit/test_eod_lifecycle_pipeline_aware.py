"""
EOD lifecycle gate: does anything still open require THIS SERVICE? (25-Aug-2026)

The gate used to ask "are there ANY open positions?" — a product-blind count. That
coupled the two pipelines through the process lifecycle: a carried DELIVERY position
held the whole service open past window_end, so the unit was still `active` at 08:15,
token_watcher read "running — nothing to do", NO BOOT happened, the 15:15 SOFT_KILL
never auto-cleared, and the next day took NO ENTRIES IN BOTH BOOKS.

T-1 is the case that FAILS on the pre-fix tree — see the module docstring note in
docs/audit/PREDICTION_eodlifecycle_25-Aug-2026.md for the frozen falsifiers.
"""
from __future__ import annotations

import hashlib
import inspect
import re
from datetime import datetime, time as _time

import pytest

import main as main_mod
from core.state_store import StateStore

WINDOW_END = _time(17, 35)
# Past window_end, which config guarantees is past eod_squareoff_time.
NOW = datetime(2026, 8, 25, 18, 0, 0)

# The real 24-Aug book shape, used verbatim so the fixture carries production
# identity, not merely production shape.
BALUFORGE = {
    "trade_id": "trd_00971394a203459fb6061d0e4c0aff06",
    "symbol": "BALUFORGE", "status": "OPEN",
    "strategy": "positional_swing_long", "entry_product": "CNC",
}
KAMATHOTEL = {
    "trade_id": "trd_50cf00402d624ab9a69cf62429bcc75b",
    "symbol": "KAMATHOTEL", "status": "OPEN",
    "strategy": "positional_swing_long", "entry_product": "CNC",
}
# An intraday position that could NOT be squared off — the circuit-locked case.
# Zerodha's auto square-off can fail, and REJECTED_CIRCUIT_PROXIMITY shows circuit
# stocks are in this universe.
STUCK_MIS = {
    "trade_id": "trd_circuit_locked_0001",
    "symbol": "RBZJEWEL", "status": "OPEN",
    "strategy": "vwap_bounce_long", "entry_product": "MIS",
}

INTENTS = {
    "positional_swing_long": "DELIVERY",
    "positional_momentum_long": "DELIVERY",
    "vwap_bounce_long": "INTRADAY",
    "first_pullback_long": "INTRADAY",
}


def _intent_fn(name):
    return INTENTS.get(name)


class _FakeStore:
    """Concrete sequence in, concrete count out — no mock semantics."""

    def __init__(self, rows):
        self._rows = list(rows)

    def get_active_positions_with_identity(self):
        return list(self._rows)

    def count_active_positions(self):
        # The product-blind count, exactly as the real one behaves.
        return len(self._rows)


def _due(rows, on_unresolved=None):
    return main_mod._eod_self_exit_due(
        _FakeStore(rows), NOW, WINDOW_END, None,
        strategy_intent_fn=_intent_fn,
        on_unresolved=on_unresolved,
    )


# ── T-1 ──────────────────────────────────────────────────────────────────────
def test_t1_delivery_only_carry_lets_the_service_exit():
    """A carried delivery book must NOT hold the service open.

    This is the case that fails today: pre-fix, these two rows are counted by the
    product-blind count and the service stays up — which is what silently disables
    the intraday pipeline the next morning.
    """
    due, active = _due([BALUFORGE, KAMATHOTEL])
    assert active == 0, (
        "a cleanly identified delivery carry must not require this service — "
        "its stop and target are a broker-side OCO GTT"
    )
    assert due is True, "the service must self-exit on a delivery-only carry"


def test_t1b_the_product_blind_count_still_sees_those_same_two_positions():
    """The control for T-1: the old count is NOT zero for that book.

    Without this, T-1 could pass merely because the fixture was empty.
    """
    assert _FakeStore([BALUFORGE, KAMATHOTEL]).count_active_positions() == 2


# ── T-2 ──────────────────────────────────────────────────────────────────────
def test_t2_intraday_survivor_keeps_the_service_up():
    """An MIS position still open past its square-off is abnormal survival:
    unprotected, and now an unplanned delivery obligation."""
    due, active = _due([STUCK_MIS])
    assert active == 1
    assert due is False, "the service must stay up for a surviving intraday position"


# ── T-3 ──────────────────────────────────────────────────────────────────────
def test_t3_identity_conflict_stays_up_and_reports():
    """Strategy says DELIVERY, the broker ENTRY product says MIS.

    Both sources exist and disagree. The gate must not guess, must not shut down,
    and must not stay up SILENTLY — a silent stay-up is half a failure.
    """
    conflicted = dict(BALUFORGE, entry_product="MIS")  # strategy -> DELIVERY
    seen = []
    due, active = _due([conflicted], on_unresolved=seen.append)

    assert due is False, "an identity conflict must keep the service up"
    assert active == 1

    assert seen, "the conflict must be REPORTED, not silently absorbed"
    notes = [n for batch in seen for n in batch]
    assert len(notes) == 1
    note = notes[0]
    assert note["pipeline"] == main_mod._IDENTITY_CONFLICT
    # The report must carry BOTH sources so an operator can actually resolve it.
    assert note["strategy_intent"] == "DELIVERY"
    assert note["entry_product"] == "MIS"
    assert note["symbol"] == "BALUFORGE"


def test_t3b_unresolved_identity_also_stays_up_and_reports():
    """Neither source usable — a trade with no ENTRY order row and no known
    strategy. Distinct from CONFLICT, same conservative outcome."""
    orphan = {
        "trade_id": "trd_orphan", "symbol": "GHOST", "status": "PENDING_FILL",
        "strategy": None, "entry_product": None,
    }
    seen = []
    due, active = _due([orphan], on_unresolved=seen.append)
    assert (due, active) == (False, 1)
    notes = [n for batch in seen for n in batch]
    assert notes[0]["pipeline"] == main_mod._IDENTITY_UNRESOLVED


# ── T-4 ──────────────────────────────────────────────────────────────────────
def test_t4_mixed_book_stays_up_and_counts_only_the_stuck_mis():
    due, active = _due([BALUFORGE, STUCK_MIS])
    assert due is False, "a stuck MIS keeps the service up even beside a delivery carry"
    assert active == 1, "only the MIS position requires this service"


# ── T-5 ──────────────────────────────────────────────────────────────────────
def test_t5_empty_book_exits_exactly_as_before():
    assert _due([]) == (True, 0)


def test_t5b_before_window_end_is_unchanged():
    """No regression on the early-return: before window_end it never queries."""
    early = datetime(2026, 8, 25, 12, 0, 0)
    assert main_mod._eod_self_exit_due(
        _FakeStore([BALUFORGE]), early, WINDOW_END, None,
        strategy_intent_fn=_intent_fn,
    ) == (False, -1)


def test_t5c_flatten_gate_still_wins_and_is_checked_first():
    """M-C8 is inherited untouched: a flatten in progress holds the process open
    regardless of what the position identities say."""
    due, active = main_mod._eod_self_exit_due(
        _FakeStore([]), NOW, WINDOW_END, lambda: True,
        strategy_intent_fn=_intent_fn,
    )
    assert due is False
    assert active == main_mod._ACTIVE_FLATTEN_IN_PROGRESS


# ── the "looks like zero" hazard ─────────────────────────────────────────────
def test_a_non_sequence_read_falls_back_instead_of_reading_as_flat():
    """An object that is iterable but EMPTY must never read as 'flat'.

    A MagicMock iterates as [] — so a stub store would have made the gate exit out
    from under a live position. The gate requires a concrete sequence and falls
    back to the product-blind count, which counts MORE and so stays up.
    """
    class _BadStore:
        def get_active_positions_with_identity(self):
            return iter([])          # iterable, empty, not a list/tuple

        def count_active_positions(self):
            return 2                 # the truth: two positions are open

    due, active = main_mod._eod_self_exit_due(
        _BadStore(), NOW, WINDOW_END, None, strategy_intent_fn=_intent_fn,
    )
    assert (due, active) == (False, 2), "must fall back, not read as flat"


def test_without_a_strategy_resolver_the_old_product_blind_path_is_used():
    """The PRIMARY identity source is the strategy intent. Absent it, the gate does
    not silently run in a degraded product-only mode."""
    assert main_mod._eod_self_exit_due(
        _FakeStore([BALUFORGE, KAMATHOTEL]), NOW, WINDOW_END, None,
    ) == (False, 2)


# ── the pure resolver ────────────────────────────────────────────────────────
@pytest.mark.parametrize("intent,product,expected", [
    ("DELIVERY", "CNC", "DELIVERY"),
    ("INTRADAY", "MIS", "INTRADAY"),
    ("DELIVERY", "MIS", main_mod._IDENTITY_CONFLICT),
    ("INTRADAY", "CNC", main_mod._IDENTITY_CONFLICT),
    ("DELIVERY", None, "DELIVERY"),          # primary alone resolves
    (None, "CNC", "DELIVERY"),               # second source alone resolves
    (None, None, main_mod._IDENTITY_UNRESOLVED),
    ("NONSENSE", None, main_mod._IDENTITY_UNRESOLVED),
    # PRODUCT_TO_INTENT maps NRML -> DELIVERY, so IDENTITY resolution says
    # DELIVERY. Whether that buys an early shutdown is a separate question —
    # see the protection tests below. Identity and protection are not the same.
    (None, "NRML", "DELIVERY"),
])
def test_resolver_truth_table(intent, product, expected):
    assert main_mod._resolve_position_pipeline(intent, product) == expected


# ── identity is not protection ───────────────────────────────────────────────
def test_only_cnc_is_treated_as_broker_protected():
    """NRML resolves to the DELIVERY pipeline but this system never places it and
    never puts an OCO behind it, so it must NOT let the service exit.

    core/constants.py is explicit that NRML is an unrecognised anomaly the
    emergency sites flatten loudly — "never silently spare the unknown".
    """
    nrml = dict(BALUFORGE, entry_product="NRML", strategy=None)
    seen = []
    due, active = _due([nrml], on_unresolved=seen.append)
    assert due is False, "an NRML position must not buy an early shutdown"
    assert active == 1
    notes = [n for batch in seen for n in batch]
    assert notes[0]["reason"] == "delivery_without_broker_protection"


def test_delivery_strategy_with_no_product_row_still_keeps_the_service_up():
    """Strategy says DELIVERY but there is no ENTRY product to confirm CNC.
    We cannot verify broker protection, so we do not assume it."""
    unconfirmed = dict(BALUFORGE, entry_product=None)
    due, active = _due([unconfirmed])
    assert (due, active) == (False, 1)


def test_cnc_delivery_is_the_only_case_that_releases_the_service():
    assert main_mod._position_requires_service("DELIVERY", "CNC") is False
    for product in ("NRML", "CO", "MIS", None, "", "nrml "):
        assert main_mod._position_requires_service("DELIVERY", product) is True
    for pipeline in ("INTRADAY", main_mod._IDENTITY_CONFLICT,
                     main_mod._IDENTITY_UNRESOLVED):
        assert main_mod._position_requires_service(pipeline, "CNC") is True


# ── T-6 ──────────────────────────────────────────────────────────────────────
def test_t6_count_active_positions_is_still_product_blind():
    """The shared function must NOT become pipeline-aware.

    It also feeds the risk_engine OPEN_POSITIONS cap and the portfolio allocator;
    making it pipeline-aware would move a live risk cap in the same stroke. This
    asserts the PROPERTY (no product/strategy/join in its body), not a digest.
    """
    src = inspect.getsource(StateStore.count_active_positions)
    body = src.split('"""')[-1]          # skip the docstring
    lowered = body.lower()
    for forbidden in ("product", "strategy", "join", "orders"):
        assert forbidden not in lowered, (
            f"count_active_positions must stay product-blind; found {forbidden!r}"
        )
    assert "OPEN" in body and "PARTIAL" in body and "PENDING_FILL" in body


def test_t6b_the_other_two_consumers_still_call_the_shared_count():
    """risk_engine and the portfolio allocator must be untouched by this unit."""
    import capital.risk_engine as risk_engine
    import allocation.portfolio_allocator as allocator

    risk_src = inspect.getsource(risk_engine)
    assert "count_active_positions" in risk_src, (
        "the risk_engine OPEN_POSITIONS cap must still use the shared count"
    )
    alloc_src = inspect.getsource(allocator)
    assert "active_count_fn" in alloc_src

    # Neither may have grown a pipeline dimension in this unit.
    for name, src in (("risk_engine", risk_src), ("portfolio_allocator", alloc_src)):
        assert "get_active_positions_with_identity" not in src, (
            f"{name} must not consume the new pipeline-aware read"
        )


def test_t6c_the_new_read_is_consumed_only_by_the_eod_gate():
    """One consumer, by construction."""
    main_src = inspect.getsource(main_mod)
    call_sites = re.findall(r"\.get_active_positions_with_identity\(", main_src)
    assert len(call_sites) == 1, (
        f"expected exactly one call site in main.py, found {len(call_sites)}"
    )
