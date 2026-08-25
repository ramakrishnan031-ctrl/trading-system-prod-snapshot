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

import ast
import builtins
import hashlib
import inspect
import re
import threading
from datetime import datetime, time as _time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import main as main_mod
from core.state_store import StateStore
from strategies.loader import StrategyLoader

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


# ─────────────────────────────────────────────────────────────────────────────
# P-3 — IS THE PRODUCTION PATH ACTUALLY WIRED? (25-Aug-2026)
#
# Every test above this line supplies its OWN resolver. That is right for testing
# the gate's LOGIC, but it means none of them can detect the single failure that
# would make this whole unit inert: production not passing a resolver at all.
# With strategy_intent_fn=None the gate falls back to the product-blind count and
# the lifecycle coupling this unit exists to remove is back — deployed, gated
# green, reporting nothing, and changing nothing.
#
# THE CHAIN THAT MUST HOLD, END TO END:
#   _main_locked  --strategy_intent_fn=<lambda>-->  _start_eod_self_exit_thread
#   _start_eod_self_exit_thread  --strategy_intent_fn=-->  _eod_self_exit_due
# Break EITHER link and the fix is inert. Both are pinned below.
#
# INSTRUMENT — the AST of main.py, plus EXECUTION of the production expression
# itself against the shipped strategy YAMLs. Not grep (formatting-fragile), and
# not a re-implementation of the lambda (a copy can drift from production and
# still go green). The resolver exercised here is the one parsed out of main.py.
#
# WHAT A RED HERE MEANS — AND WHAT IT DOES NOT:
#   RED  = production stopped passing a WORKING strategy resolver into the EOD
#          lifecycle gate. The gate is now product-blind, so a delivery carry
#          holds the service open past window_end, the unit is still `active` at
#          08:15, token_watcher reads "running - nothing to do", the 15:15
#          SOFT_KILL never auto-clears, and the next trading day takes NO ENTRIES
#          IN EITHER BOOK. That is a live trading defect.
#          Fix the wiring. Do NOT edit the test to match.
#   NOT  = someone moved or reformatted a line. These assertions are deliberately
#          insensitive to formatting, to the lambda's parameter name, and to the
#          name of the strategies mapping it closes over.
#          Renaming the KEYWORD is a real wiring change and SHOULD fail here: the
#          keyword is the contract between _main_locked and the gate.
# ─────────────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(main_mod.__file__).resolve().parent
_STRATEGIES_DIR = _REPO_ROOT / "config" / "strategies"


def _main_ast() -> ast.Module:
    return ast.parse(Path(main_mod.__file__).read_text(encoding="utf-8"))


def _fn_node(name: str) -> ast.FunctionDef:
    for n in ast.walk(_main_ast()):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name}() not found in main.py")


def _sole_call_kwargs(container: ast.FunctionDef, callee: str) -> dict:
    """The keyword arguments of the ONE call to `callee` inside `container`."""
    calls = [
        n for n in ast.walk(container)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
    ]
    assert len(calls) == 1, (
        f"expected exactly one call to {callee}() inside {container.name}(), "
        f"found {len(calls)} - the wiring this test pins is no longer unique"
    )
    return {k.arg: k.value for k in calls[0].keywords}


def _load_shipped_strategies(force_intraday_only: bool = False) -> dict:
    """Load the SHIPPED strategy YAMLs with the SAME loader production uses.

    Deterministic: config files only. No DB, no VM, no network, no clock, no
    market date, no open position.
    """
    return StrategyLoader().load_all_strategies(
        _STRATEGIES_DIR, force_intraday_only=force_intraday_only,
    )


def _compile_production_resolver(node: ast.AST):
    """Compile the resolver EXPRESSION lifted from main.py's call site.

    The one free name it closes over (the loaded strategies mapping, whatever it
    happens to be called) is bound to the real shipped config, so what runs here
    is the production expression itself rather than a copy of it.
    """
    src = ast.unparse(node)
    params = set()
    if isinstance(node, ast.Lambda):
        a = node.args
        params = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    free = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    free -= params
    free -= set(dir(builtins))
    assert free, (
        "the production resolver closes over NO names, so it is not consulting the "
        "loaded strategy config at all - it can never resolve an intent, and the "
        f"EOD gate is effectively product-blind. Source: {src!r}"
    )
    assert len(free) == 1, (
        "expected the production resolver to close over exactly one name - the "
        f"loaded strategies mapping - but it references {sorted(free)}. That is a "
        f"wiring change: review it, do not silently widen this test. Source: {src!r}"
    )
    mapping_name = free.pop()
    strategies = _load_shipped_strategies()
    g = {"__builtins__": builtins, mapping_name: strategies}
    expr = ast.Expression(body=node)
    ast.fix_missing_locations(expr)
    resolver = eval(compile(expr, "<main.py::_main_locked>", "eval"), g)  # noqa: S307
    return resolver, strategies, src


# -- P-3a: the call site passes something, and it is not None -----------------
def test_p3a_production_passes_a_resolver_that_is_not_none():
    """_main_locked must hand the gate a real resolver.

    RED here = the EOD gate is running product-blind in production (see the block
    comment above). NOT a formatting failure.
    """
    kw = _sole_call_kwargs(_fn_node("_main_locked"), "_start_eod_self_exit_thread")
    assert "strategy_intent_fn" in kw, (
        "_main_locked no longer passes strategy_intent_fn to the EOD gate - the "
        "gate has silently reverted to the product-blind count and a delivery "
        "carry will again hold the service open overnight"
    )
    node = kw["strategy_intent_fn"]
    assert not (isinstance(node, ast.Constant) and node.value is None), (
        "strategy_intent_fn is passed as a literal None - that is exactly the "
        "product-blind fallback, i.e. this unit deployed and inert"
    )


# -- P-3b: and it actually RESOLVES (the "wrong reason" guard) ----------------
def test_p3b_the_production_resolver_actually_resolves_every_shipped_strategy():
    """Presence of the kwarg is NOT enough: `lambda name: None` would satisfy it
    and still leave the gate product-blind.

    So execute the production expression against the shipped config and require
    it to return each strategy's DECLARED intent. Two degenerate resolvers die
    here: one that always returns None (fails the real names) and one that always
    returns a fixed intent (fails the unknown name).
    """
    kw = _sole_call_kwargs(_fn_node("_main_locked"), "_start_eod_self_exit_thread")
    resolver, strategies, src = _compile_production_resolver(kw["strategy_intent_fn"])

    assert strategies, "no strategy YAMLs shipped - this test would be vacuous"

    for name, cfg in sorted(strategies.items()):
        got = resolver(name)
        assert got == cfg.intent, (
            f"the production resolver returned {got!r} for strategy {name!r} but "
            f"its YAML declares intent {cfg.intent!r} - resolver source: {src}"
        )
        assert got in ("INTRADAY", "DELIVERY"), (
            f"{name!r} resolved to {got!r}, which is neither pipeline"
        )

    # A constant resolver must not survive: an unknown strategy has no intent.
    assert resolver("__no_such_strategy__") is None, (
        "the production resolver returned an intent for a strategy that does not "
        f"exist - it is not consulting the strategy config. Source: {src}"
    )

    # This test reads intents with force_intraday_only=False while production
    # passes the configured value. That is only sound because the loader PRESERVES
    # the declared intent (Option A, 10-Jul-2026 - the old load-time DELIVERY ->
    # INTRADAY rewrite was removed). Pin that assumption rather than rely on it.
    forced = {n: c.intent for n, c in _load_shipped_strategies(True).items()}
    assert forced == {n: c.intent for n, c in strategies.items()}, (
        "the loader now rewrites intent under force_intraday_only - the EOD gate's "
        "PRIMARY identity source would change meaning with the breaker on"
    )


# -- P-3c: the second link - the thread forwards it to the decision function --
def test_p3c_the_thread_forwards_the_resolver_to_the_decision_function():
    """A resolver that reaches the thread but not _eod_self_exit_due is just as
    inert as one that was never passed. This pins the second link.

    RED here = the gate stopped receiving the resolver it was given.
    """
    fnode = _fn_node("_start_eod_self_exit_thread")
    kw = _sole_call_kwargs(fnode, "_eod_self_exit_due")
    assert "strategy_intent_fn" in kw, (
        "_start_eod_self_exit_thread no longer forwards strategy_intent_fn to "
        "_eod_self_exit_due - the gate is product-blind again"
    )
    node = kw["strategy_intent_fn"]
    params = {x.arg for x in (*fnode.args.posonlyargs, *fnode.args.args,
                              *fnode.args.kwonlyargs)}
    assert isinstance(node, ast.Name) and node.id == "strategy_intent_fn", (
        "the forwarded value is no longer the strategy_intent_fn parameter itself "
        f"(got {ast.unparse(node)!r})"
    )
    assert node.id in params, (
        "strategy_intent_fn is forwarded but is not a parameter of "
        "_start_eod_self_exit_thread - it cannot be coming from _main_locked"
    )


# -- fix (2): the degraded mode announces itself ------------------------------
# The gate's own block comment says silently running product-blind "is exactly
# the kind of unannounced behaviour change this gate must not make" -- but with
# no resolver it did precisely that: skipped the pipeline-aware path and fell
# through to the product-blind count without a single log line. The read-failure
# path was loud; this one was mute. These two tests pin the announcement AND its
# control, so it cannot regress to silence and cannot start crying wolf.


def _start_and_settle(**kwargs):
    """Run the REAL thread starter with an already-set shutdown event.

    _run() checks that event before doing anything else, so the thread returns
    immediately: this exercises the startup announcement with no poll loop, no
    clock wait and no store read.
    """
    ev = threading.Event()
    ev.set()
    log = MagicMock()
    main_mod._start_eod_self_exit_thread(
        store=MagicMock(), notifier=None, mode="LIVE", log=log,
        market_windows=None, shutdown_event=ev, window_end=WINDOW_END,
        poll_interval_sec=1, **kwargs
    )
    return log


def test_p3d_a_missing_resolver_is_announced_and_names_the_consequence():
    """No resolver => the gate is product-blind. Say so, at CRITICAL, once.

    A bare "resolver missing" would not do: whoever reads it at 08:15 needs to
    know what it COSTS -- that a delivery carry will hold the service open and
    the next trading day takes no entries in either book.
    """
    log = _start_and_settle()
    assert log.critical.called, (
        "a missing resolver silently degrades the gate to the product-blind "
        "count -- it must announce itself"
    )
    msg = log.critical.call_args[0][0]
    assert "PRODUCT-BLIND" in msg, "the announcement must name the degraded mode"
    assert "NO ENTRIES IN EITHER BOOK" in msg, (
        "the announcement must name the CONSEQUENCE, not just the condition"
    )


def test_p3e_a_wired_resolver_announces_nothing():
    """The control. Without this, the test above would pass on a log.critical
    that fires unconditionally -- which in production would be a CRITICAL every
    single service start."""
    log = _start_and_settle(strategy_intent_fn=_intent_fn)
    assert not log.critical.called, (
        "the normal, correctly-wired path must be silent"
    )
