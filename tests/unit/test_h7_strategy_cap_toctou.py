"""H-7 (Wave-5): per-strategy position cap is atomic (check+reserve inside portfolio_lock).

Root cause (audit H-7): the per-strategy open-count check ran OUTSIDE fm.portfolio_lock
and counted only DB OPEN/PARTIAL with no in-flight/reservation compensation, so a
Chartink burst (one webhook -> N symbols -> up to worker_count workers on the SAME
strategy) let every worker read the same stale count and all pass -> a cap of N blown.
The buggy block was triplicated across the 3 pipeline entry paths.

Fix: ONE deduped `SignalProcessor._enforce_strategy_position_cap`, called INSIDE
portfolio_lock immediately before reserve(), counting the strategy's OPEN/PARTIAL trades
+ live fund_manager reservations (authoritative, mirrors risk_engine's global FIX-185).

Real collaborators: a real StateStore (schema-backed tmp DB), a real FundManager (real
portfolio_lock RLock + reserve() + count_live_reservations_for_strategy), and the REAL
SignalProcessor._enforce_strategy_position_cap method. Only unused SignalProcessor
collaborators are omitted (the method reads only self._store and self._fm, so the
instance is built via object.__new__ with those two set). No mock of the lock, the
count, or the reservation — the genuine critical section runs under real thread
contention.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from capital.fund_manager import FundManager
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import today_ist
from signals.signal_processor import SignalProcessor, _PipelineReject


# ── real-collaborator harness ────────────────────────────────────────────────
def _make_fm(store: StateStore) -> FundManager:
    fm = FundManager(
        state_store=store,
        bus=EventBus(),
        logger=logging.getLogger("test_h7"),
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10,
    )
    # Ample capital so the CAP — not capital — is the binding constraint under test.
    fm.initialize(10_000_000.0)
    return fm


def _make_sp(store: StateStore, fm: FundManager) -> SignalProcessor:
    """A SignalProcessor whose _enforce_strategy_position_cap reads only _store + _fm.
    object.__new__ skips the heavy __init__ (queue/bus/screener/... unused here)."""
    sp = object.__new__(SignalProcessor)
    sp._store = store
    sp._fm = fm
    return sp


def _insert_open_trade(store: StateStore, strategy: str, symbol: str, status: str = "OPEN") -> str:
    """Insert a real signals->trades row pair (schema-backed) in the given status."""
    ts = f"{today_ist()}T10:00:00+05:30"
    trade_id = str(uuid.uuid4())
    signal_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, ?, 'test', ?, ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (signal_id, symbol, strategy, ts, ts, ts, f"fp-{signal_id}", today_ist()),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned, qty_filled,
                entry_target_price, sl_initial, tgt_initial, margin_reserved, risk_amount,
                created_at, status, order_protocol, updated_at)
               VALUES (?, ?, ?, 'LONG', ?, 10, 10, 100.0, 95.0, 110.0, 2000.0, 500.0,
                       ?, ?, 'LIMIT_TRIPLE', ?)""",
            (trade_id, signal_id, symbol, strategy, ts, status, ts),
        )
    return trade_id


@pytest.fixture()
def make_env(tmp_path):
    """Factory: a fresh (store, fm, sp) trio per call (independent DB + capital)."""
    seq = {"n": 0}

    def _factory():
        seq["n"] += 1
        store = StateStore(db_path=tmp_path / f"h7_{seq['n']}.db")
        fm = _make_fm(store)
        return SimpleNamespace(store=store, fm=fm, sp=_make_sp(store, fm))

    return _factory


# ── Test 1: the TOCTOU race — RED (old outside-lock) vs GREEN (new inside-lock) ──
def test_toctou_burst_old_pattern_blows_cap_new_pattern_enforces(make_env):
    STRAT = "gap_go_long"
    CAP = 2
    N = 6  # burst > cap (a Chartink one-webhook-many-symbols fan-out)
    strat_obj = SimpleNamespace(max_concurrent_positions=CAP)

    # ---------- RED: the pre-H-7 OUTSIDE-lock check lets the whole burst through ----------
    # Faithful reproduction of the removed code: count DB OPEN/PARTIAL only, BEFORE the
    # lock. During a burst no trade rows have materialised yet, so every worker reads 0,
    # all pass the check, and all reserve -> cap blown. (Real fm.portfolio_lock + reserve.)
    red = make_env()
    red_barrier = threading.Barrier(N)
    red_results: list = [None] * N

    def _old_worker(i: int) -> None:
        red_barrier.wait()  # release all workers together = maximal contention
        row = red.store.fetch_one(
            "SELECT COUNT(*) AS n FROM trades WHERE strategy=? AND status IN ('OPEN','PARTIAL')",
            (STRAT,),
        )
        open_count = int(row["n"]) if row and row["n"] is not None else 0
        if open_count >= CAP:            # the stale, outside-lock gate
            red_results[i] = "rejected"
            return
        time.sleep(0.02)                 # widen the check->reserve window
        with red.fm.portfolio_lock:
            res = red.fm.reserve(f"RSYM{i}", 1, 100.0, "INTRADAY", f"rsig{i}", strategy=STRAT)
        red_results[i] = "ok" if res.success else "rejected"

    red_threads = [threading.Thread(target=_old_worker, args=(i,)) for i in range(N)]
    for t in red_threads:
        t.start()
    for t in red_threads:
        t.join(timeout=15)
    red_ok = red_results.count("ok")
    assert red_ok > CAP, (
        f"RED expectation: the OLD outside-lock check should BLOW the cap under a burst; "
        f"got {red_ok} ok (cap={CAP}), results={red_results}"
    )

    # ---------- GREEN: the H-7 inside-lock method admits exactly `cap` ----------
    green = make_env()
    green_barrier = threading.Barrier(N)
    green_results: list = [None] * N

    def _new_worker(i: int) -> None:
        green_barrier.wait()
        # EXACTLY the fixed pipeline critical section: check + reserve inside the lock.
        with green.fm.portfolio_lock:
            try:
                green.sp._enforce_strategy_position_cap(STRAT, strat_obj)
            except _PipelineReject:
                green_results[i] = "rejected"
                return
            res = green.fm.reserve(f"GSYM{i}", 1, 100.0, "INTRADAY", f"gsig{i}", strategy=STRAT)
        green_results[i] = "ok" if res.success else "rejected"

    green_threads = [threading.Thread(target=_new_worker, args=(i,)) for i in range(N)]
    for t in green_threads:
        t.start()
    for t in green_threads:
        t.join(timeout=15)

    # Test 4 (no deadlock / no hang): every worker finished within the timeout.
    assert all(not t.is_alive() for t in green_threads), "deadlock/hang: a worker did not finish"

    green_ok = green_results.count("ok")
    assert green_ok == CAP, (
        f"GREEN expectation: exactly cap={CAP} reservations admitted under the burst; "
        f"got {green_ok} ok, results={green_results}"
    )
    # And the fund_manager agrees the cap was respected.
    assert green.fm.count_live_reservations_for_strategy(STRAT) == CAP


# ── Test 2: dedup — ONE cap-check location, every reserve path routes through it ──
def test_dedup_single_cap_check_and_all_paths_route_through_it():
    src = Path("signals/signal_processor.py").read_text(encoding="utf-8")
    # The reject label is raised in exactly one place (the consolidated method).
    assert src.count("STRATEGY_POSITION_LIMIT") == 1
    # Exactly one definition, invoked by all three pipeline paths.
    assert src.count("def _enforce_strategy_position_cap") == 1
    assert src.count("self._enforce_strategy_position_cap(") == 3
    # The old outside-lock count query is fully gone (no surviving duplicate).
    assert "SELECT COUNT(*) AS n FROM trades WHERE strategy" not in src
    # Every reserve() call tags the strategy so reservations are counted per-strategy.
    assert src.count("self._fm.reserve(") == 3
    assert src.count("strategy=strategy_name") >= 3


# ── Test 3: normal cap behaviour preserved (single-path) ─────────────────────────
def test_under_cap_allows(make_env):
    env = make_env()
    strat_obj = SimpleNamespace(max_concurrent_positions=3)
    _insert_open_trade(env.store, "gap_go_long", "RELIANCE")  # 1 open, cap 3
    with env.fm.portfolio_lock:
        env.sp._enforce_strategy_position_cap("gap_go_long", strat_obj)  # no raise = allowed


def test_at_cap_rejects(make_env):
    env = make_env()
    strat_obj = SimpleNamespace(max_concurrent_positions=2)
    _insert_open_trade(env.store, "first_pullback_long", "RELIANCE")
    _insert_open_trade(env.store, "first_pullback_long", "INFY")  # 2 open == cap
    with env.fm.portfolio_lock:
        with pytest.raises(_PipelineReject) as ei:
            env.sp._enforce_strategy_position_cap("first_pullback_long", strat_obj)
    assert ei.value.check == "STRATEGY_POSITION_LIMIT"


def test_live_reservation_counts_toward_cap(make_env):
    """The authoritative part: a reservation with no DB row yet still counts (this is
    exactly the in-flight the old check missed)."""
    env = make_env()
    strat_obj = SimpleNamespace(max_concurrent_positions=2)
    _insert_open_trade(env.store, "gap_go_long", "RELIANCE")  # 1 open
    with env.fm.portfolio_lock:
        env.fm.reserve("INFY", 1, 100.0, "INTRADAY", "sigX", strategy="gap_go_long")  # +1 reserved
        # open(1) + reserved(1) == cap(2) -> the next candidate must be rejected.
        with pytest.raises(_PipelineReject):
            env.sp._enforce_strategy_position_cap("gap_go_long", strat_obj)


def test_other_strategy_not_counted(make_env):
    env = make_env()
    strat_obj = SimpleNamespace(max_concurrent_positions=2)
    _insert_open_trade(env.store, "gap_go_long", "RELIANCE")
    _insert_open_trade(env.store, "gap_go_long", "INFY")     # 2 for gap_go_long (at cap)
    env.fm.reserve("TCS", 1, 100.0, "INTRADAY", "sigY", strategy="gap_go_long")
    # A DIFFERENT strategy is unaffected by gap_go_long's positions/reservations.
    with env.fm.portfolio_lock:
        env.sp._enforce_strategy_position_cap("first_pullback_long", strat_obj)  # no raise


def test_count_live_reservations_for_strategy_is_scoped(make_env):
    env = make_env()
    env.fm.reserve("A", 1, 100.0, "INTRADAY", "s1", strategy="gap_go_long")
    env.fm.reserve("B", 1, 100.0, "INTRADAY", "s2", strategy="gap_go_long")
    env.fm.reserve("C", 1, 100.0, "INTRADAY", "s3", strategy="first_pullback_long")
    assert env.fm.count_live_reservations_for_strategy("gap_go_long") == 2
    assert env.fm.count_live_reservations_for_strategy("first_pullback_long") == 1
    assert env.fm.count_live_reservations_for_strategy("unknown") == 0
    # Untagged reservations (recovery/replay path) do not pollute a strategy's count.
    env.fm.reserve("D", 1, 100.0, "INTRADAY", "s4")  # strategy defaults to None
    assert env.fm.count_live_reservations_for_strategy("gap_go_long") == 2
