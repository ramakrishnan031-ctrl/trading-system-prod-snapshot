"""
tests/unit/test_entry_gate.py

Validates screening/entry_gate.py against EG1-EG15.

Test strategy:
  - Direct _check_one() calls for deterministic unit tests (most tests).
  - start()/stop() integration tests only where poll-loop behaviour matters.
  - Mock state_store (no DB required).
  - Mock quote_fn with deterministic responses.
  - Time control via setting added_at in the past (no real sleep needed for
    timeout tests).

Run: python -m pytest tests/unit/test_entry_gate.py -v
Or:  python tests/unit/test_entry_gate.py
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.time_authority import now_ist
from screening.entry_gate import EntryGate, WatchEntry


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

@dataclass
class _MockQuote:
    last_price: float
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    upper_circuit_limit: Optional[float] = None
    lower_circuit_limit: Optional[float] = None


class _MockStateStore:
    def __init__(self, raise_exc: Optional[Exception] = None):
        self.updates: List[tuple] = []
        self.raise_exc = raise_exc

    def update_signal_status(self, signal_id: str, status: str,
                             reason: str = None, ts: str = None) -> None:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.updates.append((signal_id, status))


class _NullLogger:
    def __init__(self):
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.infos: List[str] = []

    def debug(self, *a, **kw): pass
    def info(self, msg, *a, **kw): self.infos.append(str(msg))
    def warning(self, msg, *a, **kw): self.warnings.append(str(msg))
    def error(self, msg, *a, **kw): self.errors.append(str(msg))
    def critical(self, *a, **kw): pass


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_entry(
    signal_id: str = "sig_001",
    symbol: str = "RELIANCE",
    direction: str = "LONG",
    entry_price: float = 1000.0,
    tolerance_pct: float = 0.01,     # 1% -> zone 990-1010
    timeout_sec: int = 180,
    added_at: Optional[datetime] = None,
    extras: Optional[dict] = None,
) -> WatchEntry:
    if added_at is None:
        added_at = now_ist().replace(tzinfo=None)
    return WatchEntry(
        signal_id=signal_id,
        symbol=symbol,
        direction=direction,
        trigger_price=entry_price + 20.0,
        entry_price=entry_price,
        sl_price=entry_price * 0.98,
        tgt_price=entry_price * 1.04,
        tolerance_pct=tolerance_pct,
        timeout_sec=timeout_sec,
        strategy_name="test_strategy",
        tier="HIGH",
        scanner_name="test_scanner",
        intent="INTRADAY",
        added_at=added_at,
        extras=extras or {},
    )


def _make_gate(
    quote_fn: Optional[Callable] = None,
    on_release: Optional[Callable] = None,
    store: Optional[_MockStateStore] = None,
    logger: Optional[_NullLogger] = None,
    poll_interval_sec: float = 0.05,
    worker_count: int = 2,
) -> tuple:
    if quote_fn is None:
        quote_fn = lambda symbols: {}
    if on_release is None:
        on_release = lambda entry, reason: None
    if store is None:
        store = _MockStateStore()
    if logger is None:
        logger = _NullLogger()
    gate = EntryGate(
        quote_fn=quote_fn,
        logger=logger,
        state_store=store,
        on_release=on_release,
        poll_interval_sec=poll_interval_sec,
        worker_count=worker_count,
    )
    return gate, store, logger


def _in_zone_quote(entry: WatchEntry, offset: float = 0.0) -> Callable:
    """Returns a quote_fn giving ltp = entry_price + offset (in zone when |offset| <= tolerance)."""
    ltp = entry.entry_price + offset
    def quote_fn(symbols):
        return {s: _MockQuote(last_price=ltp) for s in symbols}
    return quote_fn


def _outside_zone_quote(entry: WatchEntry, overshoot: float = 100.0) -> Callable:
    """Returns a quote_fn giving ltp far below entry zone."""
    ltp = entry.entry_price - overshoot
    def quote_fn(symbols):
        return {s: _MockQuote(last_price=ltp) for s in symbols}
    return quote_fn


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

def test_constructor_invalid_poll_interval():
    """poll_interval_sec <= 0 raises ValueError."""
    try:
        EntryGate(
            quote_fn=lambda s: {},
            logger=_NullLogger(),
            state_store=_MockStateStore(),
            on_release=lambda e, r: None,
            poll_interval_sec=0,
        )
        assert False, "Expected ValueError"
    except ValueError:
        pass
    try:
        EntryGate(
            quote_fn=lambda s: {},
            logger=_NullLogger(),
            state_store=_MockStateStore(),
            on_release=lambda e, r: None,
            poll_interval_sec=-1.0,
        )
        assert False, "Expected ValueError for negative"
    except ValueError:
        pass
    print("  OK poll_interval_sec <= 0 raises ValueError")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_start_stop_is_running():
    """start() -> is_running() True; stop() -> False."""
    gate, _, _ = _make_gate()
    assert not gate.is_running()
    gate.start()
    assert gate.is_running()
    gate.stop()
    assert not gate.is_running()
    print("  OK start/stop/is_running lifecycle")


def test_double_start_is_noop():
    """start() while already running logs warning and does not raise."""
    log = _NullLogger()
    gate, _, _ = _make_gate(logger=log)
    gate.start()
    gate.start()  # second call is no-op
    assert gate.is_running()
    assert any("already running" in w for w in log.warnings), \
        f"No warning logged for double start: {log.warnings}"
    gate.stop()
    print("  OK double start() is no-op with warning")


def test_stop_before_start_is_noop():
    """stop() before start() does not raise."""
    gate, _, _ = _make_gate()
    gate.stop()  # should not raise
    assert not gate.is_running()
    print("  OK stop() before start() is no-op")


def test_stop_joins_within_5s():
    """stop() completes within 5s even with entries in watchlist."""
    gate, _, _ = _make_gate()
    gate.start()
    for i in range(5):
        gate.add(_make_entry(signal_id=f"sig_stop_{i:03d}", symbol=f"SYM{i}"))
    t0 = time.monotonic()
    gate.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"stop() took {elapsed:.2f}s"
    print(f"  OK stop() with 5 entries took {elapsed:.3f}s")


# ---------------------------------------------------------------------------
# Add / Remove / API (EG4, EG11)
# ---------------------------------------------------------------------------

def test_add_inserts_entry():
    """add() inserts entry; size() and watchlist() reflect it."""
    gate, _, _ = _make_gate()
    entry = _make_entry("sig_add_001")
    gate.add(entry)
    assert gate.size() == 1
    wl = gate.watchlist()
    assert len(wl) == 1
    assert wl[0].signal_id == "sig_add_001"
    print("  OK add() inserts entry, size=1, watchlist correct")


def test_add_duplicate_raises():
    """add() with duplicate signal_id raises ValueError (EG11)."""
    gate, _, _ = _make_gate()
    entry = _make_entry("sig_dup_001")
    gate.add(entry)
    try:
        gate.add(entry)
        assert False, "Expected ValueError"
    except ValueError:
        pass
    assert gate.size() == 1  # only one in watchlist
    print("  OK add() duplicate raises ValueError, watchlist unchanged")


def test_remove_returns_true():
    """remove() by signal_id returns True and entry is gone."""
    gate, _, _ = _make_gate()
    entry = _make_entry("sig_rem_001")
    gate.add(entry)
    result = gate.remove("sig_rem_001")
    assert result is True
    assert gate.size() == 0
    print("  OK remove() known entry -> True, size=0")


def test_remove_unknown_returns_false():
    """remove() for unknown signal_id returns False without error (EG11)."""
    gate, _, _ = _make_gate()
    result = gate.remove("does_not_exist")
    assert result is False
    print("  OK remove() unknown -> False, no exception")


def test_remove_clears_failure_count():
    """remove() after quote failures clears the failure counter."""
    gate, _, _ = _make_gate(quote_fn=lambda s: (_ for _ in ()).throw(ConnectionError("fail")))
    entry = _make_entry("sig_rfail_001")
    gate.add(entry)
    gate._check_one(entry)   # fail count -> 1
    gate._check_one(entry)   # fail count -> 2
    assert gate._quote_failures.get("sig_rfail_001", 0) == 2
    gate.remove("sig_rfail_001")
    assert gate._quote_failures.get("sig_rfail_001", 0) == 0
    print("  OK remove() clears quote failure count")


def test_watchlist_returns_copy():
    """watchlist() returns a copy; mutating it does not affect internal state."""
    gate, _, _ = _make_gate()
    gate.add(_make_entry("sig_wl_001"))
    wl = gate.watchlist()
    wl.clear()
    assert gate.size() == 1, "Internal watchlist should not be affected"
    print("  OK watchlist() returns defensive copy")


def test_size_multiple():
    """size() tracks multiple entries correctly."""
    gate, _, _ = _make_gate()
    for i in range(5):
        gate.add(_make_entry(signal_id=f"sig_sz_{i:03d}", symbol=f"SYM{i}"))
    assert gate.size() == 5
    gate.remove("sig_sz_002")
    assert gate.size() == 4
    print("  OK size() tracks add/remove correctly")


def test_add_while_gate_stopped():
    """add() works even when gate has not been started."""
    gate, _, _ = _make_gate()
    entry = _make_entry("sig_stopped_add")
    gate.add(entry)  # must not raise
    assert gate.size() == 1
    print("  OK add() while gate stopped: no exception, entry in watchlist")


# ---------------------------------------------------------------------------
# Price trigger (EG5)
# ---------------------------------------------------------------------------

def test_price_hit_long_in_zone():
    """LONG: ltp inside tolerance zone -> PRICE_HIT release."""
    released = []
    gate, store, _ = _make_gate(
        on_release=lambda e, r: released.append((e.signal_id, r)),
    )
    entry = _make_entry("sig_ph_long", direction="LONG", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    # ltp = 1005.0 -> lower=990, upper=1010. 990 <= 1005 <= 1010. HIT.
    gate._quote_fn = _in_zone_quote(entry, offset=5.0)
    gate._check_one(entry)
    assert released == [("sig_ph_long", "PRICE_HIT")]
    assert gate.size() == 0
    print("  OK LONG price in zone -> PRICE_HIT, entry removed")


def test_price_no_hit_long_outside_zone():
    """LONG: ltp far below zone -> no release."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_no_ph_long", direction="LONG", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    gate._quote_fn = _outside_zone_quote(entry, overshoot=50.0)  # ltp=950
    gate._check_one(entry)
    assert released == [], f"Unexpected release: {released}"
    assert gate.size() == 1
    print("  OK LONG price outside zone (950) -> no release")


def test_price_hit_short_in_zone():
    """SHORT: ltp inside tolerance zone -> PRICE_HIT release."""
    released = []
    gate, store, _ = _make_gate(
        on_release=lambda e, r: released.append((e.signal_id, r)),
    )
    entry = _make_entry("sig_ph_short", direction="SHORT", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    # SHORT pullback: price retraces UP into zone. ltp=1005 is still in symmetric zone.
    gate._quote_fn = _in_zone_quote(entry, offset=5.0)
    gate._check_one(entry)
    assert released == [("sig_ph_short", "PRICE_HIT")]
    print("  OK SHORT price in zone -> PRICE_HIT")


def test_price_hit_boundary_lower():
    """ltp exactly at lower bound -> HIT (EG5: lower <= ltp <= upper)."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_lower", direction="LONG", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    lower = entry.entry_price - entry.entry_price * entry.tolerance_pct  # 990.0
    gate._quote_fn = lambda s: {sym: _MockQuote(last_price=lower) for sym in s}
    gate._check_one(entry)
    assert released == ["PRICE_HIT"], f"Expected HIT at lower bound, got: {released}"
    print(f"  OK ltp=lower ({lower}) -> PRICE_HIT (boundary inclusive)")


def test_price_hit_boundary_upper():
    """ltp exactly at upper bound -> HIT."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_upper", direction="LONG", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    upper = entry.entry_price + entry.entry_price * entry.tolerance_pct  # 1010.0
    gate._quote_fn = lambda s: {sym: _MockQuote(last_price=upper) for sym in s}
    gate._check_one(entry)
    assert released == ["PRICE_HIT"], f"Expected HIT at upper bound, got: {released}"
    print(f"  OK ltp=upper ({upper}) -> PRICE_HIT (boundary inclusive)")


def test_price_no_hit_just_outside_lower():
    """ltp just below lower bound -> no HIT."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_out_lower", direction="LONG", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    lower = entry.entry_price - entry.entry_price * entry.tolerance_pct  # 990.0
    gate._quote_fn = lambda s: {sym: _MockQuote(last_price=lower - 0.01) for sym in s}
    gate._check_one(entry)
    assert released == [], f"Unexpected HIT below lower: {released}"
    print(f"  OK ltp={lower-0.01} (just outside lower) -> no HIT")


def test_price_no_hit_just_outside_upper():
    """ltp just above upper bound -> no HIT."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_out_upper", direction="LONG", entry_price=1000.0,
                        tolerance_pct=0.01)
    gate.add(entry)
    upper = entry.entry_price + entry.entry_price * entry.tolerance_pct  # 1010.0
    gate._quote_fn = lambda s: {sym: _MockQuote(last_price=upper + 0.01) for sym in s}
    gate._check_one(entry)
    assert released == [], f"Unexpected HIT above upper: {released}"
    print(f"  OK ltp={upper+0.01} (just outside upper) -> no HIT")


def test_tolerance_zone_calculation():
    """1% tolerance of 1000 = zone [990, 1010] (EG5)."""
    gate, _, _ = _make_gate()
    entry = _make_entry("sig_tol", entry_price=1000.0, tolerance_pct=0.01)
    tolerance = entry.entry_price * entry.tolerance_pct
    lower = entry.entry_price - tolerance
    upper = entry.entry_price + tolerance
    assert abs(lower - 990.0) < 0.001, f"Expected lower=990, got {lower}"
    assert abs(upper - 1010.0) < 0.001, f"Expected upper=1010, got {upper}"
    print(f"  OK tolerance zone: 1% of 1000 -> [{lower}, {upper}]")


def test_tolerance_pct_zero_exact_price_only():
    """tolerance_pct=0 -> zone is a single point; only exact ltp triggers HIT."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_zero_tol", entry_price=1000.0, tolerance_pct=0.0)
    gate.add(entry)

    # ltp = 1000.001 -> no HIT
    gate._quote_fn = lambda s: {sym: _MockQuote(last_price=1000.001) for sym in s}
    gate._check_one(entry)
    assert released == [], "Should not trigger with ltp != entry_price"

    # ltp = 1000.0 exact -> HIT
    gate._quote_fn = lambda s: {sym: _MockQuote(last_price=1000.0) for sym in s}
    gate._check_one(entry)
    assert released == ["PRICE_HIT"], f"Expected HIT at exact price: {released}"
    print("  OK tolerance_pct=0 -> only exact price triggers HIT")


# ---------------------------------------------------------------------------
# Timeout (EG6)
# ---------------------------------------------------------------------------

def test_timeout_releases_entry():
    """Entry with added_at far in the past > timeout_sec -> TIMEOUT release."""
    released = []
    gate, store, _ = _make_gate(
        on_release=lambda e, r: released.append((e.signal_id, r)),
    )
    old_time = now_ist().replace(tzinfo=None) - timedelta(seconds=200)
    entry = _make_entry("sig_timeout_001", timeout_sec=100, added_at=old_time)
    gate.add(entry)
    gate._check_one(entry)
    assert released == [("sig_timeout_001", "TIMEOUT")]
    assert gate.size() == 0
    assert any(("sig_timeout_001", "GATE_RELEASED_TIMEOUT") == u for u in store.updates)
    print("  OK timeout entry -> TIMEOUT release, store updated")


def test_price_hit_before_timeout():
    """Entry that would timeout but hits price first -> PRICE_HIT, not TIMEOUT."""
    released = []
    gate, _, _ = _make_gate(
        quote_fn=_in_zone_quote(_make_entry("sig_pre_tmo")),
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_pre_tmo", timeout_sec=100)
    gate.add(entry)
    gate._quote_fn = _in_zone_quote(entry)
    gate._check_one(entry)
    assert released == ["PRICE_HIT"], f"Expected PRICE_HIT, got: {released}"
    print("  OK price hit before timeout -> PRICE_HIT (not TIMEOUT)")


def test_multiple_entries_independent_timeout():
    """Each WatchEntry has its own independent timeout."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append((e.signal_id, r)),
    )
    old_time = now_ist().replace(tzinfo=None) - timedelta(seconds=200)
    fresh_time = now_ist().replace(tzinfo=None)

    timed_out = _make_entry("sig_tmo_a", timeout_sec=100, added_at=old_time)
    fresh = _make_entry("sig_tmo_b", timeout_sec=9999, added_at=fresh_time)

    gate.add(timed_out)
    gate.add(fresh)

    gate._quote_fn = _outside_zone_quote(timed_out, overshoot=200.0)
    gate._check_one(timed_out)
    gate._check_one(fresh)

    # Only timed_out should be released
    reasons = dict(released)
    assert reasons.get("sig_tmo_a") == "TIMEOUT", f"Expected TIMEOUT for A: {reasons}"
    assert "sig_tmo_b" not in reasons, f"B should not be released: {reasons}"
    assert gate.size() == 1
    print("  OK independent timeouts: A timed out, B still in watchlist")


def test_not_yet_timed_out_no_release():
    """Entry with fresh added_at and no price hit -> no release."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_fresh", timeout_sec=9999)
    gate.add(entry)
    gate._quote_fn = _outside_zone_quote(entry, overshoot=200.0)
    gate._check_one(entry)
    assert released == [], f"Unexpected release for fresh entry: {released}"
    assert gate.size() == 1
    print("  OK fresh entry with no price hit -> no release")


# ---------------------------------------------------------------------------
# Quote failures (EG9)
# ---------------------------------------------------------------------------

def _failing_quote_fn(symbols):
    raise ConnectionError("quote service down")


def test_quote_failure_once_no_release():
    """First quote failure -> skip cycle, entry remains, warning logged."""
    released = []
    log = _NullLogger()
    gate, _, _ = _make_gate(
        quote_fn=_failing_quote_fn,
        on_release=lambda e, r: released.append(r),
        logger=log,
    )
    entry = _make_entry("sig_qf1")
    gate.add(entry)
    gate._check_one(entry)
    assert released == [], "Should not release on first quote failure"
    assert gate.size() == 1
    assert gate._quote_failures.get("sig_qf1") == 1
    assert any("1/3" in w for w in log.warnings), f"Warning missing: {log.warnings}"
    print("  OK 1st quote failure -> skip, entry stays, warning logged")


def test_quote_failure_two_no_release():
    """Two consecutive failures -> still no release, count=2."""
    released = []
    gate, _, _ = _make_gate(
        quote_fn=_failing_quote_fn,
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_qf2")
    gate.add(entry)
    gate._check_one(entry)
    gate._check_one(entry)
    assert released == [], "Should not release after 2 failures"
    assert gate._quote_failures.get("sig_qf2") == 2
    print("  OK 2 consecutive failures -> no release, count=2")


def test_quote_failure_3_consecutive_releases_unavailable():
    """3 consecutive quote failures -> QUOTE_UNAVAILABLE release (EG9)."""
    released = []
    store = _MockStateStore()
    log = _NullLogger()
    gate, _, _ = _make_gate(
        quote_fn=_failing_quote_fn,
        on_release=lambda e, r: released.append((e.signal_id, r)),
        store=store,
        logger=log,
    )
    entry = _make_entry("sig_qf3")
    gate.add(entry)
    gate._check_one(entry)  # fail 1
    gate._check_one(entry)  # fail 2
    gate._check_one(entry)  # fail 3 -> release
    assert released == [("sig_qf3", "QUOTE_UNAVAILABLE")]
    assert gate.size() == 0
    assert any(("sig_qf3", "GATE_RELEASED_QUOTE_UNAVAILABLE") == u for u in store.updates)
    assert any("sig_qf3" in e for e in log.errors)
    print("  OK 3 consecutive failures -> QUOTE_UNAVAILABLE, error logged")


def test_quote_failure_resets_on_success():
    """Successful quote after 2 failures resets consecutive count to 0."""
    released = []
    fail_count = [0]

    def intermittent_quote(symbols):
        fail_count[0] += 1
        if fail_count[0] <= 2:
            raise ConnectionError("fail")
        # 3rd call succeeds but ltp is outside zone
        return {s: _MockQuote(last_price=500.0) for s in symbols}

    gate, _, _ = _make_gate(
        quote_fn=intermittent_quote,
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_reset", entry_price=1000.0, tolerance_pct=0.01)
    gate.add(entry)
    gate._check_one(entry)  # fail 1 -> count=1
    gate._check_one(entry)  # fail 2 -> count=2
    gate._check_one(entry)  # success -> count reset to 0
    assert gate._quote_failures.get("sig_reset", 0) == 0
    assert released == [], "Should not release after reset"
    assert gate.size() == 1
    print("  OK quote success after 2 failures resets count, no release")


def test_quote_failure_symbol_a_no_affect_b():
    """Quote failure for A does not affect B's processing."""
    released = []
    entry_a = _make_entry("sig_a", symbol="SYM_A", entry_price=1000.0)
    entry_b = _make_entry("sig_b", symbol="SYM_B", entry_price=2000.0, tolerance_pct=0.01)

    def quote_fn(symbols):
        result = {}
        for sym in symbols:
            if sym == "SYM_A":
                raise ConnectionError("A failed")
            else:
                result[sym] = _MockQuote(last_price=2005.0)  # in zone for B
        return result

    gate, _, _ = _make_gate(
        quote_fn=quote_fn,
        on_release=lambda e, r: released.append((e.signal_id, r)),
    )
    gate.add(entry_a)
    gate.add(entry_b)

    # Call check_one for each separately (as poll loop would)
    gate._check_one(entry_a)  # A: fail 1
    gate._check_one(entry_b)  # B: should hit price

    assert ("sig_b", "PRICE_HIT") in released, f"B should be released: {released}"
    assert all(sid != "sig_a" for sid, _ in released), f"A should not be released: {released}"
    assert gate._quote_failures.get("sig_a") == 1
    print("  OK quote failure on SYM_A doesn't affect SYM_B price trigger")


# ---------------------------------------------------------------------------
# Release protocol (EG7)
# ---------------------------------------------------------------------------

def test_on_release_called_with_entry_and_reason_price_hit():
    """on_release(entry, reason) invoked correctly on PRICE_HIT (EG7)."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append((e, r)),
    )
    entry = _make_entry("sig_cb_ph")
    gate.add(entry)
    gate._quote_fn = _in_zone_quote(entry)
    gate._check_one(entry)
    assert len(released) == 1
    rel_entry, rel_reason = released[0]
    assert rel_entry.signal_id == "sig_cb_ph"
    assert rel_reason == "PRICE_HIT"
    print("  OK on_release called with (entry, 'PRICE_HIT')")


def test_on_release_called_on_timeout():
    """on_release invoked with reason='TIMEOUT' on timeout (EG7)."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    old_time = now_ist().replace(tzinfo=None) - timedelta(seconds=200)
    entry = _make_entry("sig_cb_tmo", timeout_sec=100, added_at=old_time)
    gate.add(entry)
    gate._check_one(entry)
    assert released == ["TIMEOUT"]
    print("  OK on_release called with 'TIMEOUT'")


def test_on_release_raises_logged_poll_continues():
    """on_release callback that raises is caught; error logged; gate not crashed."""
    log = _NullLogger()

    def bad_release(entry, reason):
        raise RuntimeError("callback exploded")

    gate, _, _ = _make_gate(on_release=bad_release, logger=log)
    entry = _make_entry("sig_bad_cb")
    gate.add(entry)
    gate._quote_fn = _in_zone_quote(entry)
    gate._check_one(entry)  # should not raise despite bad callback

    # Entry was removed from watchlist
    assert gate.size() == 0
    # Error was logged
    assert any("sig_bad_cb" in e for e in log.errors), f"Error not logged: {log.errors}"
    print("  OK on_release raises -> error logged, gate continues, entry removed")


def test_entry_removed_before_on_release():
    """Watchlist entry is removed BEFORE on_release is called (EG7 step 1)."""
    watchlist_size_at_callback = []

    def on_release(entry, reason):
        # At this point, entry should already be gone from watchlist
        watchlist_size_at_callback.append(gate.size())

    gate, _, _ = _make_gate(on_release=on_release)
    entry = _make_entry("sig_order")
    gate.add(entry)
    gate._quote_fn = _in_zone_quote(entry)
    gate._check_one(entry)

    assert watchlist_size_at_callback == [0], \
        f"Entry should be removed before callback: size was {watchlist_size_at_callback}"
    print("  OK entry removed from watchlist before on_release is called")


def test_release_idempotent_concurrent():
    """_release() called twice for same entry (race): second call is no-op."""
    released = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released.append(r),
    )
    entry = _make_entry("sig_idem")
    gate.add(entry)
    gate._release(entry, "PRICE_HIT")
    gate._release(entry, "PRICE_HIT")  # second call: entry already removed
    assert len(released) == 1, f"on_release should be called once, got {len(released)}"
    print("  OK _release() idempotent: second call is no-op")


# ---------------------------------------------------------------------------
# State store persistence (EG7 step 2)
# ---------------------------------------------------------------------------

def test_state_store_price_hit_status():
    """GATE_RELEASED_PRICE_HIT written to store on PRICE_HIT (EG7)."""
    store = _MockStateStore()
    gate, _, _ = _make_gate(store=store)
    entry = _make_entry("sig_ss_ph")
    gate.add(entry)
    gate._quote_fn = _in_zone_quote(entry)
    gate._check_one(entry)
    assert ("sig_ss_ph", "GATE_RELEASED_PRICE_HIT") in store.updates
    print("  OK GATE_RELEASED_PRICE_HIT written on PRICE_HIT")


def test_state_store_timeout_status():
    """GATE_RELEASED_TIMEOUT written to store on TIMEOUT (EG7)."""
    store = _MockStateStore()
    gate, _, _ = _make_gate(store=store)
    old_time = now_ist().replace(tzinfo=None) - timedelta(seconds=200)
    entry = _make_entry("sig_ss_tmo", timeout_sec=100, added_at=old_time)
    gate.add(entry)
    gate._check_one(entry)
    assert ("sig_ss_tmo", "GATE_RELEASED_TIMEOUT") in store.updates
    print("  OK GATE_RELEASED_TIMEOUT written on TIMEOUT")


def test_state_store_quote_unavailable_status():
    """GATE_RELEASED_QUOTE_UNAVAILABLE written on 3 consecutive failures."""
    store = _MockStateStore()
    gate, _, _ = _make_gate(quote_fn=_failing_quote_fn, store=store)
    entry = _make_entry("sig_ss_qu")
    gate.add(entry)
    gate._check_one(entry)
    gate._check_one(entry)
    gate._check_one(entry)
    assert ("sig_ss_qu", "GATE_RELEASED_QUOTE_UNAVAILABLE") in store.updates
    print("  OK GATE_RELEASED_QUOTE_UNAVAILABLE written after 3 failures")


def test_state_store_failure_callback_still_called():
    """state_store write failure: error logged but on_release still invoked (EG7)."""
    released = []
    log = _NullLogger()
    store = _MockStateStore(raise_exc=RuntimeError("DB full"))
    gate, _, _ = _make_gate(
        store=store,
        on_release=lambda e, r: released.append(r),
        logger=log,
    )
    entry = _make_entry("sig_db_fail")
    gate.add(entry)
    gate._quote_fn = _in_zone_quote(entry)
    gate._check_one(entry)

    # on_release still invoked despite DB failure
    assert released == ["PRICE_HIT"], f"Callback should fire: {released}"
    # Error logged
    assert any("sig_db_fail" in e for e in log.errors), f"Error not logged: {log.errors}"
    print("  OK DB write failure -> error logged, on_release still called")


# ---------------------------------------------------------------------------
# Thread safety (EG10)
# ---------------------------------------------------------------------------

def test_concurrent_add_remove_no_races():
    """5 threads each add+remove their own entries: final watchlist empty, no exceptions."""
    gate, _, _ = _make_gate()
    errors = []

    def worker(tid):
        try:
            for i in range(10):
                sig_id = f"sig_t{tid}_{i:02d}"
                entry = _make_entry(sig_id, symbol=f"SYM{tid}")
                gate.add(entry)
                gate.remove(sig_id)
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Errors in concurrent access: {errors}"
    assert gate.size() == 0, f"Expected empty watchlist, got {gate.size()}"
    print("  OK 5-thread concurrent add/remove: no races, watchlist empty")


def test_poll_snapshot_does_not_block_add():
    """Poll thread snapshots watchlist; add() while poll runs is safe."""
    # This is a structural test: add() can happen while _poll_loop holds the lock
    # for snapshot. We verify no deadlock by using start/stop with concurrent adds.
    gate, _, _ = _make_gate(poll_interval_sec=0.02)
    errors = []

    def adder():
        try:
            for i in range(20):
                gate.add(_make_entry(f"sig_poll_{i:03d}", symbol=f"SYM{i}"))
                time.sleep(0.001)
        except Exception as exc:
            errors.append(str(exc))

    gate.start()
    t = threading.Thread(target=adder)
    t.start()
    t.join(timeout=5.0)
    gate.stop()

    assert errors == [], f"Errors during concurrent poll+add: {errors}"
    print("  OK add() concurrent with poll loop: no deadlock, no errors")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_watchlist_poll_is_noop():
    """start/stop with empty watchlist completes cleanly."""
    gate, _, _ = _make_gate(poll_interval_sec=0.02)
    gate.start()
    time.sleep(0.05)
    gate.stop()
    assert not gate.is_running()
    print("  OK empty watchlist: poll loop is a no-op, clean stop")


def test_100_entries_all_polled():
    """100 entries in watchlist, all with price in zone -> all released via _check_one."""
    released_ids = []
    gate, _, _ = _make_gate(
        on_release=lambda e, r: released_ids.append(e.signal_id),
    )
    entries = []
    for i in range(100):
        entry = _make_entry(f"sig_{i:04d}", symbol=f"SYM{i:03d}",
                            entry_price=float(1000 + i))
        entries.append(entry)
        gate.add(entry)
        gate._quote_fn = _in_zone_quote(entry)

    # Set a single quote_fn that hits all entries
    def all_in_zone(symbols):
        result = {}
        for sym in symbols:
            # Find entry by symbol
            for e in entries:
                if e.symbol == sym:
                    result[sym] = _MockQuote(last_price=e.entry_price)
                    break
        return result

    gate._quote_fn = all_in_zone
    for entry in entries:
        gate._check_one(entry)

    assert len(released_ids) == 100, f"Expected 100 releases, got {len(released_ids)}"
    assert gate.size() == 0
    print(f"  OK 100 entries, all polled via _check_one: {len(released_ids)} released")


# ---------------------------------------------------------------------------
# Integration: start/stop poll loop
# ---------------------------------------------------------------------------

def test_poll_loop_releases_price_hit():
    """Integration: start gate, add entry, poll loop fires PRICE_HIT callback."""
    released = threading.Event()
    release_reason = []

    def on_release(entry, reason):
        release_reason.append(reason)
        released.set()

    entry = _make_entry("sig_int_ph", entry_price=1000.0, tolerance_pct=0.01)
    gate, _, _ = _make_gate(
        quote_fn=_in_zone_quote(entry),
        on_release=on_release,
        poll_interval_sec=0.05,
    )
    gate.start()
    gate.add(entry)

    fired = released.wait(timeout=2.0)
    gate.stop()

    assert fired, "Poll loop did not fire PRICE_HIT within 2s"
    assert release_reason == ["PRICE_HIT"]
    print("  OK integration: poll loop fires PRICE_HIT callback")


def test_poll_loop_releases_timeout():
    """Integration: entry with past added_at -> poll loop fires TIMEOUT."""
    released = threading.Event()
    release_reason = []

    def on_release(entry, reason):
        release_reason.append(reason)
        released.set()

    old_time = now_ist().replace(tzinfo=None) - timedelta(seconds=200)
    entry = _make_entry("sig_int_tmo", timeout_sec=100, added_at=old_time)
    gate, _, _ = _make_gate(
        quote_fn=_outside_zone_quote(entry, overshoot=200.0),
        on_release=on_release,
        poll_interval_sec=0.05,
    )
    gate.start()
    gate.add(entry)

    fired = released.wait(timeout=2.0)
    gate.stop()

    assert fired, "Poll loop did not fire TIMEOUT within 2s"
    assert release_reason == ["TIMEOUT"]
    print("  OK integration: poll loop fires TIMEOUT callback")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def run_all_tests() -> int:
    tests = [
        test_constructor_invalid_poll_interval,
        test_start_stop_is_running,
        test_double_start_is_noop,
        test_stop_before_start_is_noop,
        test_stop_joins_within_5s,
        test_add_inserts_entry,
        test_add_duplicate_raises,
        test_remove_returns_true,
        test_remove_unknown_returns_false,
        test_remove_clears_failure_count,
        test_watchlist_returns_copy,
        test_size_multiple,
        test_add_while_gate_stopped,
        test_price_hit_long_in_zone,
        test_price_no_hit_long_outside_zone,
        test_price_hit_short_in_zone,
        test_price_hit_boundary_lower,
        test_price_hit_boundary_upper,
        test_price_no_hit_just_outside_lower,
        test_price_no_hit_just_outside_upper,
        test_tolerance_zone_calculation,
        test_tolerance_pct_zero_exact_price_only,
        test_timeout_releases_entry,
        test_price_hit_before_timeout,
        test_multiple_entries_independent_timeout,
        test_not_yet_timed_out_no_release,
        test_quote_failure_once_no_release,
        test_quote_failure_two_no_release,
        test_quote_failure_3_consecutive_releases_unavailable,
        test_quote_failure_resets_on_success,
        test_quote_failure_symbol_a_no_affect_b,
        test_on_release_called_with_entry_and_reason_price_hit,
        test_on_release_called_on_timeout,
        test_on_release_raises_logged_poll_continues,
        test_entry_removed_before_on_release,
        test_release_idempotent_concurrent,
        test_state_store_price_hit_status,
        test_state_store_timeout_status,
        test_state_store_quote_unavailable_status,
        test_state_store_failure_callback_still_called,
        test_concurrent_add_remove_no_races,
        test_poll_snapshot_does_not_block_add,
        test_empty_watchlist_poll_is_noop,
        test_100_entries_all_polled,
        test_poll_loop_releases_price_hit,
        test_poll_loop_releases_timeout,
    ]

    print("=" * 70)
    print("entry_gate.py -- Test Suite (EG1-EG15)")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as exc:
            failed.append((test.__name__, f"AssertionError: {exc}"))
            print(f"  FAIL: {exc}")
        except Exception as exc:
            failed.append((test.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
