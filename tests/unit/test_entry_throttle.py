"""
tests/unit/test_entry_throttle.py — Bug G (full): EntryThrottle.

Covers the spec scenarios with an injectable fake clock (no real sleeps):
min-gap, burst window + reset, per-symbol cooldown (same vs different symbol),
disabled, per-reason metrics, and thread-safety (concurrent admit never exceeds
burst_max).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from signals.entry_throttle import EntryThrottle


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _thr(min_gap=0.0, burst_window=60.0, burst_max=0, per_symbol=0.0):
    clk = _Clock()
    return EntryThrottle(min_gap_sec=min_gap, burst_window_sec=burst_window,
                         burst_max=burst_max, per_symbol_cooldown_sec=per_symbol,
                         clock=clk), clk


# ── min-gap ──────────────────────────────────────────────────────────────────

def test_first_entry_passes():
    t, _ = _thr(min_gap=20, burst_max=3)
    assert t.admit("A").allowed is True


def test_second_within_min_gap_throttled():
    t, clk = _thr(min_gap=20, burst_max=3)
    assert t.admit("A").allowed
    clk.advance(5)
    r = t.admit("B")
    assert not r.allowed and r.category == "min_gap"


def test_third_after_gap_within_window_passes():
    t, clk = _thr(min_gap=20, burst_window=60, burst_max=3)
    assert t.admit("A").allowed           # t=1000
    clk.advance(21); assert t.admit("B").allowed   # t=1021 (>20s gap)
    clk.advance(21); assert t.admit("C").allowed   # t=1042 (3rd, still <3 in window? 3 now)


def test_fourth_within_burst_window_throttled():
    t, clk = _thr(min_gap=0, burst_window=60, burst_max=3)
    assert t.admit("A").allowed
    assert t.admit("B").allowed
    assert t.admit("C").allowed
    r = t.admit("D")          # 4th within 60s
    assert not r.allowed and r.category == "burst"


def test_burst_resets_after_window():
    t, clk = _thr(min_gap=0, burst_window=60, burst_max=3)
    for s in "ABC":
        assert t.admit(s).allowed
    assert not t.admit("D").allowed       # burst hit
    clk.advance(61)                       # window passes
    assert t.admit("E").allowed           # resets


# ── per-symbol cooldown ──────────────────────────────────────────────────────

def test_per_symbol_cooldown_blocks_same_symbol():
    t, clk = _thr(per_symbol=300)
    assert t.admit("RELIANCE").allowed
    clk.advance(120)                      # 2 min < 5 min cooldown
    r = t.admit("RELIANCE")
    assert not r.allowed and r.category == "per_symbol"


def test_per_symbol_cooldown_allows_different_symbol():
    t, clk = _thr(per_symbol=300)
    assert t.admit("RELIANCE").allowed
    clk.advance(10)
    assert t.admit("TCS").allowed         # different symbol, not cooled down


def test_per_symbol_cooldown_expires():
    t, clk = _thr(per_symbol=300)
    assert t.admit("RELIANCE").allowed
    clk.advance(301)
    assert t.admit("RELIANCE").allowed     # cooldown expired


def test_global_takes_precedence_over_per_symbol():
    # min_gap blocks before per_symbol is even evaluated
    t, clk = _thr(min_gap=20, per_symbol=300)
    assert t.admit("A").allowed
    clk.advance(5)
    r = t.admit("B")                       # different symbol, but min_gap blocks
    assert not r.allowed and r.category == "min_gap"


# ── disabled + metrics ───────────────────────────────────────────────────────

def test_disabled_admits_all():
    t, _ = _thr()                          # all 0 -> off
    assert not t.enabled
    for _ in range(10):
        assert t.admit("A").allowed


def test_metrics_per_reason():
    t, clk = _thr(min_gap=20, burst_window=60, burst_max=3, per_symbol=300)
    t.admit("A")            # admitted
    clk.advance(5); t.admit("B")           # min_gap reject
    clk.advance(20); t.admit("A")          # per_symbol reject (A within 300s)
    m = t.metrics()
    assert m["entries_admitted"] == 1
    assert m["entries_throttled_min_gap"] == 1
    assert m["entries_throttled_per_symbol"] == 1
    assert m["entries_throttled_burst"] == 0


# ── thread-safety ────────────────────────────────────────────────────────────

def test_replay_19jun_burst_cannot_recur():
    """19-Jun incident: 5 entries fired at t=0,1,2,3,4s. With the production config
    (min_gap=20, burst=3/60s), only the FIRST is admitted — min_gap blocks 2-5.
    The 5-in-5s burst can no longer reach the broker."""
    t, clk = _thr(min_gap=20, burst_window=60, burst_max=3, per_symbol=300)
    admitted = 0
    for i in range(5):                     # 5 signals, 1 second apart
        if t.admit(f"SYM{i}").allowed:
            admitted += 1
        clk.advance(1)
    assert admitted == 1, f"burst must be throttled to 1 in 5s, got {admitted}"
    # spaced signals later are admitted up to the burst cap (3 per 60s)
    clk.advance(20); assert t.admit("X").allowed   # t~25, gap ok
    clk.advance(20); assert t.admit("Y").allowed   # t~45 (3rd in window)
    clk.advance(1);  assert not t.admit("Z").allowed  # 4th in 60s -> burst


def test_concurrent_admit_never_exceeds_burst_max():
    # 50 threads race to admit within the same window; burst_max=5 -> exactly 5 pass.
    t = EntryThrottle(min_gap_sec=0, burst_window_sec=60, burst_max=5)
    results = []
    lock = threading.Lock()

    def worker(i):
        r = t.admit(f"S{i}")
        with lock:
            results.append(r.allowed)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(results) == 5, f"expected exactly 5 admitted, got {sum(results)}"
