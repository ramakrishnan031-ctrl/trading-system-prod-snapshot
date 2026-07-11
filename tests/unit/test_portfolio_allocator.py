"""
tests/unit/test_portfolio_allocator.py — V3 03.05 (Step 6b).

Covers the three acceptance gates:
  • G-OFF       — the signal_processor admission path is byte-identical whether the
                  allocator is None (off) or a SHADOW observer (shadow falls through to
                  the SAME fused admit; the observer only buffers a copy).
  • G-SHADOW-READY — shadow computes the three regret metrics and NEVER reserves/places
                  (the enforce admit/reserve callbacks are never invoked in shadow).
  • enforce (built, not activated) — an in-scope candidate is handed off (fused admit
                  skipped, claim retained) and admit_prepared reuses _admit_and_place.

Plus units for the ranker (A7), window buffer, greedy simulation + the (inert-by-default)
shared concentration cap / long-short skew, the regret math, and the config validators.
"""
from __future__ import annotations

import logging

import pytest

from allocation import PortfolioAllocator, ScoredCandidate, WindowBuffer
from allocation.ranker import arrival_ordered, rank_candidates
from allocation.models import tier_rank
from core.config_loader import PortfolioAllocatorConfig

from tests.unit.test_signal_processor import (
    _make_proc, _now_tup, _MockOrderPlacer, _MockFundManager, _insert_queued_signal,
)

_LOG = logging.getLogger("test_allocator")


def _cand(sig, score, tier, epoch, side="BUY", margin=5000.0, symbol=None):
    return ScoredCandidate(
        signal_id=sig, symbol=symbol or sig.upper(), strategy_name="s", side=side,
        intent="INTRADAY", score=score, tier=tier, margin_required=margin,
        sector="X", triggered_epoch=float(epoch))


def _cfg(**kw):
    return PortfolioAllocatorConfig(**kw)


def _fixed_now(ts=1_000_000.0):
    class _T:
        def timestamp(self_inner):
            return ts
        def isoformat(self_inner):
            return "2026-07-12T10:00:00+05:30"
    return lambda: _T()


# ─────────────────────────── Ranker (A7) ───────────────────────────
def test_ranker_score_then_tier_then_epoch_then_id():
    a = _cand("a", 60, "MEDIUM", 100.0)
    b = _cand("b", 80, "HIGH", 200.0)
    c = _cand("c", 80, "HIGH", 150.0)   # == b on score+tier, earlier epoch → before b
    d = _cand("d", 80, "MEDIUM", 150.0)  # == score, lower tier → after c & b
    assert [x.signal_id for x in rank_candidates([a, b, c, d])] == ["c", "b", "d", "a"]


def test_ranker_tiebreak_is_signal_id_deterministic():
    x = _cand("x", 70, "HIGH", 100.0)
    y = _cand("y", 70, "HIGH", 100.0)   # identical except id → id ASC, replayable
    assert [c.signal_id for c in rank_candidates([y, x])] == ["x", "y"]
    assert [c.signal_id for c in rank_candidates([x, y])] == ["x", "y"]


def test_arrival_ordered_by_epoch_then_id():
    a = _cand("a", 90, "HIGH", 300.0)
    b = _cand("b", 10, "LOW", 100.0)    # earliest triggered → first (FCFS proxy)
    assert [c.signal_id for c in arrival_ordered([a, b])] == ["b", "a"]


def test_tier_rank_ordering():
    assert tier_rank("HIGH") > tier_rank("MEDIUM") > tier_rank("LOW") > tier_rank("weird")


# ─────────────────────────── WindowBuffer ───────────────────────────
def test_window_buffer_append_drain_resets():
    b = WindowBuffer()
    b.append(_cand("a", 1, "LOW", 1.0))
    b.append(_cand("b", 2, "LOW", 2.0))
    assert b.size() == 2
    drained = b.drain()
    assert [c.signal_id for c in drained] == ["a", "b"]
    assert b.size() == 0 and b.drain() == []


# ─────────────────── greedy simulation + inert caps ───────────────────
def _alloc(cfg, fm=None, active=0, admit_fn=None, reject_fn=None, v3=None):
    return PortfolioAllocator(
        config=cfg, fund_manager=fm or _MockFundManager(),
        max_open=5, active_count_fn=lambda: active, logger=_LOG,
        now_fn=_fixed_now(), portfolio_lock=None,
        enforce_admit_fn=admit_fn, enforce_reject_fn=reject_fn, v3_scope_fn=v3)


def test_simulate_slot_and_capital_limited():
    a = _alloc(_cfg())
    cands = [_cand(f"c{i}", 90 - i, "HIGH", float(i), margin=5000.0) for i in range(10)]
    # 3 free slots → at most 3 admitted regardless of ample capital
    got = a._simulate(cands, free_slots=3, avail=1_000_000.0, deployed=0.0, total=1_000_000.0)
    assert len(got) == 3
    # capital binds: only 2 fit in 12k with 5k margin each
    got2 = a._simulate(cands, free_slots=9, avail=12_000.0, deployed=0.0, total=1_000_000.0)
    assert len(got2) == 2


def test_simulate_concentration_cap_inert_by_default_then_binds():
    a_inert = _alloc(_cfg())  # cap None → inert
    cands = [_cand(f"c{i}", 90, "HIGH", float(i), margin=10_000.0) for i in range(5)]
    got = a_inert._simulate(cands, free_slots=5, avail=1_000_000.0, deployed=0.0, total=100_000.0)
    assert len(got) == 5   # inert: nothing capped

    a_cap = _alloc(_cfg(max_portfolio_deployment_pct=0.25))  # cap 25% of 100k = 25k
    got2 = a_cap._simulate(cands, free_slots=5, avail=1_000_000.0, deployed=0.0, total=100_000.0)
    assert len(got2) == 2   # 2×10k=20k ok, 3rd would exceed 25k → rejected


def test_simulate_one_per_symbol():
    a = _alloc(_cfg())
    cands = [_cand("a", 90, "HIGH", 1.0, symbol="SAME"),
             _cand("b", 80, "HIGH", 2.0, symbol="SAME")]
    got = a._simulate(cands, free_slots=5, avail=1_000_000.0, deployed=0.0, total=1_000_000.0)
    assert len(got) == 1 and got[0].signal_id == "a"


def test_simulate_skew_inert_then_binds():
    a_inert = _alloc(_cfg())
    longs = [_cand(f"L{i}", 90, "HIGH", float(i), side="BUY") for i in range(4)]
    assert len(a_inert._simulate(longs, 5, 1e9, 0.0, 1e9)) == 4  # inert: all-long ok

    a_skew = _alloc(_cfg(long_short_skew_max=0.6))  # majority capped at ceil(0.6*n)
    got = a_skew._simulate(longs, 5, 1e9, 0.0, 1e9)
    # bootstrap-lenient ceiling: L1 ok (1<=ceil.6), L2 ok (2<=ceil1.2=2), L3 rejected
    # (3>ceil1.8=2), L4 rejected → 2 admitted from an all-long batch.
    assert len(got) == 2


# ─────────────────────────── regret math (A5) ───────────────────────────
def test_regret_ranked_beats_fcfs_when_lowscore_arrives_first():
    # low-score arrives first (epoch 1) then high-score (epoch 2); ONE slot.
    low = _cand("low", 10, "LOW", 1.0, symbol="LOW")
    high = _cand("high", 99, "HIGH", 2.0, symbol="HIGH")
    a = _alloc(_cfg())
    rec = a._compute_regret([low, high], free_slots=1, avail=1e9, deployed=0.0, total=1e9)
    assert rec.n_ranked_admit == 1 and rec.n_fcfs_admit == 1
    assert rec.ranked_admit_symbols == ["HIGH"]   # ranked takes the high-score
    assert rec.fcfs_admit_symbols == ["LOW"]       # FCFS takes the early low-score
    assert rec.crowd_out == 1 and rec.starvation == 1
    assert rec.score_weighted_regret == pytest.approx(99 - 10)  # +89 quality captured


def test_regret_zero_when_capacity_ample():
    a = _alloc(_cfg())
    cands = [_cand("a", 50, "MEDIUM", 1.0), _cand("b", 60, "HIGH", 2.0)]
    rec = a._compute_regret(cands, free_slots=5, avail=1e9, deployed=0.0, total=1e9)
    assert rec.crowd_out == 0 and rec.starvation == 0
    assert rec.score_weighted_regret == pytest.approx(0.0)


# ─────────────────────── config validators ───────────────────────
def test_config_defaults_are_off_and_inert():
    c = PortfolioAllocatorConfig()
    assert c.allocator_mode == "off" and c.enforce_scope == "v3_only"
    assert c.max_portfolio_deployment_pct is None and c.long_short_skew_max is None


@pytest.mark.parametrize("bad", [
    dict(allocator_mode="on"),
    dict(enforce_scope="some"),
    dict(max_portfolio_deployment_pct=0.0),
    dict(max_portfolio_deployment_pct=1.5),
    dict(long_short_skew_max=0.4),
    dict(drain_tail_seconds=0.0),
    dict(candle_interval_seconds=1.0, drain_tail_seconds=2.0),  # tail >= interval
])
def test_config_rejects_bad_values(bad):
    with pytest.raises(Exception):
        PortfolioAllocatorConfig(**bad)


# ─────────────────── G-OFF: byte-identity of the admission path ───────────────────
def _drive(proc, tup, store):
    """Seed the QUEUED signals row (the receiver normally does this), run one signal
    synchronously through _process_one, and return its status row."""
    _insert_queued_signal(store, tup[0], symbol=tup[2], scanner=tup[1])
    proc._process_one(tup)
    return store.fetch_one(
        "SELECT status, rejection_reason FROM signals WHERE signal_id = ?", (tup[0],))


def test_off_allocator_none_admits_and_places():
    placer = _MockOrderPlacer()
    proc, sq, store = _make_proc(placer=placer)
    assert proc._allocator is None    # OFF constructs no allocator
    row = _drive(proc, _now_tup("g_off"), store)
    assert row["status"] == "PROCESSED"
    assert len(placer.calls) == 1 and placer.calls[0]["signal_id"] == "g_off"


def test_shadow_admits_identically_and_only_observes():
    """G-OFF equivalence: with a SHADOW allocator the SAME fused admit runs (place called
    once, PROCESSED) and the allocator merely BUFFERS a copy — it never admits."""
    placer = _MockOrderPlacer()
    proc, sq, store = _make_proc(placer=placer)
    admit_called = {"n": 0}
    alloc = _alloc(_cfg(allocator_mode="shadow"),
                   admit_fn=lambda c: admit_called.__setitem__("n", admit_called["n"] + 1))
    proc.set_allocator(alloc)
    row = _drive(proc, _now_tup("g_shadow"), store)
    # identical admission outcome to the OFF case:
    assert row["status"] == "PROCESSED"
    assert len(placer.calls) == 1 and placer.calls[0]["signal_id"] == "g_shadow"
    # allocator observed exactly one candidate and NEVER admitted:
    assert alloc._buffer.size() == 1
    assert admit_called["n"] == 0


def test_shadow_observe_is_guarded_and_never_raises():
    # A broken buffer must not break admission (observe swallows errors).
    placer = _MockOrderPlacer()
    proc, sq, store = _make_proc(placer=placer)
    alloc = _alloc(_cfg(allocator_mode="shadow"))
    alloc._buffer = None   # force observe() to hit an exception internally
    proc.set_allocator(alloc)
    row = _drive(proc, _now_tup("g_guard"), store)
    assert row["status"] == "PROCESSED"       # admission still succeeded
    assert len(placer.calls) == 1


# ─────────────────── G-SHADOW-READY: regret without reserve/place ───────────────────
def test_shadow_window_computes_regret_without_reserve_or_place(tmp_path):
    fm = _MockFundManager()
    admit_called = {"n": 0}
    reg_path = str(tmp_path / "regret.jsonl")
    alloc = PortfolioAllocator(
        config=_cfg(allocator_mode="shadow", regret_log_path=reg_path),
        fund_manager=fm, max_open=5, active_count_fn=lambda: 0, logger=_LOG,
        now_fn=_fixed_now(), portfolio_lock=None,
        enforce_admit_fn=lambda c: admit_called.__setitem__("n", admit_called["n"] + 1),
        enforce_reject_fn=None, v3_scope_fn=None)
    # buffer two competing candidates, then run the window
    alloc.observe(_cand("low", 10, "LOW", 1.0, symbol="LOW"))
    alloc.observe(_cand("high", 99, "HIGH", 2.0, symbol="HIGH"))
    alloc._process_window()
    # NEITHER reserve NOR place happened via the allocator worker in shadow:
    assert admit_called["n"] == 0
    assert fm.released == []            # no reservations touched
    # a regret row was persisted:
    import json
    with open(reg_path, encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    assert len(rows) == 1 and rows[0]["n_candidates"] == 2
    assert set(rows[0].keys()) >= {"crowd_out", "starvation", "score_weighted_regret"}


# ─────────────────── enforce (built, not activated) ───────────────────
def test_enforce_in_scope_hands_off_and_admit_prepared_reuses_admit(tmp_path):
    """enforce + in-scope: the fused admit is SKIPPED (handoff), the candidate is buffered,
    and admit_prepared (the worker entry) reuses _admit_and_place to actually place."""
    placer = _MockOrderPlacer()
    proc, sq, store = _make_proc(placer=placer)
    alloc = _alloc(_cfg(allocator_mode="enforce", enforce_scope="all"),
                   admit_fn=proc.admit_prepared, reject_fn=proc.reject_prepared)
    proc.set_allocator(alloc)
    _drive(proc, _now_tup("g_enf"), store)
    # fused admit was SKIPPED (handed off) — no place yet, candidate buffered:
    assert len(placer.calls) == 0
    assert alloc._buffer.size() == 1
    # now the worker admits the buffered candidate via the SHARED _admit_and_place:
    cand = alloc._buffer.drain()[0]
    placed = proc.admit_prepared(cand)
    assert placed is True
    assert len(placer.calls) == 1 and placer.calls[0]["signal_id"] == "g_enf"


def test_enforce_out_of_scope_falls_through_to_fcfs():
    """enforce but v3_only scope + a non-v3 strategy → out of scope → the fused FCFS
    admit runs unchanged (A2)."""
    placer = _MockOrderPlacer()
    proc, sq, store = _make_proc(placer=placer)
    # default v3_scope_fn → False (no strategy is v3_playbook) → out of scope
    alloc = _alloc(_cfg(allocator_mode="enforce", enforce_scope="v3_only"),
                   admit_fn=proc.admit_prepared, v3=lambda s: False)
    proc.set_allocator(alloc)
    row = _drive(proc, _now_tup("g_oos"), store)
    assert row["status"] == "PROCESSED"
    assert len(placer.calls) == 1     # FCFS admitted it normally
    assert alloc._buffer.size() == 0  # never handed off
