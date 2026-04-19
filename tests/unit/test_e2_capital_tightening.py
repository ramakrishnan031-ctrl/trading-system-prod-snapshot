"""
tests/unit/test_e2_capital_tightening.py

Regression guards for E.2 -- capital accounting tightening
  H-1  -- sync_from_broker no longer silently clamps; bucket overflow fires
          CapitalInvariantViolation + CapitalDriftDetected +
          kill_switch.hard_kill (via BL-9)
  H-3  -- FundManager.required_margin() public method; order_placer uses it
          instead of the hardcoded 0.20 (which was correct for INTRADAY 5x
          only, wrong for DELIVERY / COVER_ORDER / etc.)
  H-4  -- FundManager.initialize() soft guard against double-call; WARNING
          log + no-op protects against silent fm_ledger corruption
  H-5  -- state_store.transaction() uses BEGIN IMMEDIATE; 8 concurrent
          writers stress test bounded at 5s

Plus one regression guard on drift_handler._ESCALATING_SOURCES.

Run: python -m pytest tests/unit/test_e2_capital_tightening.py -v
Or:  python tests/unit/test_e2_capital_tightening.py
"""
from __future__ import annotations

import inspect
import logging
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import capital.drift_handler as drift_handler_mod
import core.state_store as state_store_mod
import orders.order_placer as order_placer_mod

from capital.fund_manager import FundManager, required_margin
from core.events import CapitalDriftDetected, EventBus
from core.exceptions import CapitalInvariantViolation
from core.state_store import StateStore

_SCHEMA_PATH = Path(__file__).parent.parent.parent / "core" / "schema.sql"

_LEVERAGE_MAP = {
    "INTRADAY": 5.0,
    "COVER_ORDER": 6.0,
    "DELIVERY": 1.0,
    "BRACKET_ORDER": 5.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakeKillSwitch:
    def __init__(self) -> None:
        self.hard_kill_calls: list[dict] = []
        self.soft_kill_calls: list[dict] = []

    def hard_kill(self, reason: str, triggered_by: str = "system"):
        self.hard_kill_calls.append({"reason": reason, "triggered_by": triggered_by})
        return None

    def soft_kill(self, reason: str, triggered_by: str = "system"):
        self.soft_kill_calls.append({"reason": reason, "triggered_by": triggered_by})


class _ListLogHandler(logging.Handler):
    """Captures log records for H-4 WARNING-log assertion."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _make_store(tmp_dir: Path) -> StateStore:
    return StateStore(tmp_dir / "test.db", _SCHEMA_PATH)


def _make_fm(
    store: StateStore,
    *,
    bus: Optional[EventBus] = None,
    kill_switch: Optional[_FakeKillSwitch] = None,
    logger: Optional[logging.Logger] = None,
) -> FundManager:
    return FundManager(
        state_store=store,
        bus=bus or EventBus(),
        logger=logger or logging.getLogger("test_e2_fm"),
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        daily_loss_limit=10_000.0,
        leverage_map=_LEVERAGE_MAP,
        kill_switch=kill_switch,
    )


def _fm_with_50k_intraday_used(
    store: StateStore,
    *,
    bus: Optional[EventBus] = None,
    kill_switch: Optional[_FakeKillSwitch] = None,
) -> FundManager:
    """
    Build an FM with 100k total and 50k used on intraday.
    After this: intraday_avail=20k, intraday_used=50k, positional_avail=30k.
    Sync broker to <~71k to force intraday bucket overflow.
    """
    fm = _make_fm(store, bus=bus, kill_switch=kill_switch)
    fm.initialize(100_000.0)
    res = fm.reserve("RELIANCE", qty=500, price=500.0, intent="INTRADAY")
    assert res.success, f"test setup reserve failed: {res.reason_if_failed}"
    commit = fm.commit_to_used(res.reservation_id, 500.0, 500)
    assert abs(commit.actual_margin - 50_000.0) < 0.01
    return fm


# ─────────────────────────────────────────────────────────────────────────────
# H-1 -- sync bucket overflow
# ─────────────────────────────────────────────────────────────────────────────

def test_h1_sync_bucket_overflow_raises() -> None:
    """Broker balance shrinks below bucket reserved+used -> invariant fires."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _fm_with_50k_intraday_used(store)
        raised = False
        try:
            # Sync to 30k: intraday_total=21k but used=50k -> avail=-29k
            fm.sync_from_broker(30_000.0)
        except CapitalInvariantViolation:
            raised = True
        assert raised, "sync_from_broker must raise CapitalInvariantViolation on bucket overflow"
        store.close()
    print("  OK H-1: sync_from_broker bucket overflow raises CapitalInvariantViolation")


def test_h1_sync_bucket_overflow_publishes_drift_with_correct_source_module() -> None:
    """Bucket overflow publishes CapitalDriftDetected with new source_module."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        bus = EventBus()
        received: list[CapitalDriftDetected] = []
        bus.subscribe(CapitalDriftDetected, received.append)
        fm = _fm_with_50k_intraday_used(store, bus=bus)
        try:
            fm.sync_from_broker(30_000.0)
        except CapitalInvariantViolation:
            pass
        # initialize() did not fire drift; sync publishes both total-drift
        # AND bucket-overflow. Filter for the new escalating source.
        overflow_events = [
            e for e in received if e.source_module == "fund_manager_bucket_overflow"
        ]
        assert len(overflow_events) == 1, (
            f"expected exactly 1 fund_manager_bucket_overflow event; "
            f"got {[e.source_module for e in received]}"
        )
        evt = overflow_events[0]
        # delta is the rupee magnitude (abs of the most-negative bucket avail)
        assert evt.delta > 0, f"delta must be positive magnitude; got {evt.delta}"
        assert evt.actual < 0, f"actual must be the negative bucket avail; got {evt.actual}"
        assert evt.expected == 0.0, f"expected must be 0.0; got {evt.expected}"
        store.close()
    print("  OK H-1: bucket overflow publishes CapitalDriftDetected with source_module='fund_manager_bucket_overflow'")


def test_h1_sync_bucket_overflow_fires_hard_kill() -> None:
    """BL-9 hard_kill fires via _check_invariant before the raise."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        ks = _FakeKillSwitch()
        fm = _fm_with_50k_intraday_used(store, kill_switch=ks)
        try:
            fm.sync_from_broker(30_000.0)
        except CapitalInvariantViolation:
            pass
        assert len(ks.hard_kill_calls) == 1, (
            f"expected exactly 1 hard_kill; got {len(ks.hard_kill_calls)}"
        )
        call = ks.hard_kill_calls[0]
        assert "capital_invariant_violated" in call["reason"]
        assert call["triggered_by"] == "fund_manager._check_invariant"
        store.close()
    print("  OK H-1: bucket overflow fires kill_switch.hard_kill via BL-9")


# ─────────────────────────────────────────────────────────────────────────────
# H-3 -- FundManager.required_margin + order_placer replacement
# ─────────────────────────────────────────────────────────────────────────────

def test_h3_fund_manager_required_margin_matches_free_function() -> None:
    """FM method result must match the module-level free function exactly."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _make_fm(store)
        fm.initialize(100_000.0)
        # INTRADAY 5x -- 100*500/5 = 10k (also coincidentally matches old 0.20)
        method_value = fm.required_margin(qty=100, price=500.0, intent="INTRADAY")
        free_value = required_margin(100, 500.0, "INTRADAY", _LEVERAGE_MAP)
        assert abs(method_value - free_value) < 1e-9
        assert abs(method_value - 10_000.0) < 1e-9
        # DELIVERY 1x -- 100*500/1 = 50k (differs from old hardcoded 10k)
        delivery_method = fm.required_margin(qty=100, price=500.0, intent="DELIVERY")
        delivery_free = required_margin(100, 500.0, "DELIVERY", _LEVERAGE_MAP)
        assert abs(delivery_method - delivery_free) < 1e-9
        assert abs(delivery_method - 50_000.0) < 1e-9
        # H-3 proof: old hardcode would have returned 100*500*0.20 = 10k
        old_hardcode = 100 * 500.0 * 0.20
        assert abs(delivery_method - old_hardcode) > 1.0, (
            "DELIVERY margin via FM method must differ from old 0.20 hardcode"
        )
        store.close()
    print("  OK H-3: FundManager.required_margin matches free function; DELIVERY differs from 0.20 hardcode")


def test_h3_order_placer_writes_trade_margin_from_required_margin_not_hardcode() -> None:
    """Source-level guards: no 0.20 hardcode; no _leverage_map access; uses fm.required_margin."""
    src = inspect.getsource(order_placer_mod.OrderPlacer.place)
    assert "* 0.20" not in src, (
        "H-3 regression: OrderPlacer.place must not contain hardcoded '* 0.20'"
    )
    assert "self._fm.required_margin" in src, (
        "OrderPlacer.place must use self._fm.required_margin(...)"
    )
    # Module-level guard: order_placer must not reach into FM private state.
    # Check for attribute-access patterns (._leverage_map), not the bare
    # identifier (which could legitimately appear in comments).
    module_src = inspect.getsource(order_placer_mod)
    assert "._leverage_map" not in module_src, (
        "order_placer.py must not access FundManager._leverage_map via "
        "attribute reference (H-3 boundary)"
    )
    print("  OK H-3: OrderPlacer.place uses fm.required_margin; no 0.20 hardcode; no _leverage_map access")


# ─────────────────────────────────────────────────────────────────────────────
# H-4 -- initialize() soft guard
# ─────────────────────────────────────────────────────────────────────────────

def test_h4_second_initialize_logs_warning_and_noop() -> None:
    """Second initialize() must WARN + no-op. _total must not be overwritten."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        handler = _ListLogHandler()
        logger = logging.getLogger(f"test_e2_h4_{id(handler)}")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        fm = _make_fm(store, logger=logger)
        fm.initialize(100_000.0)
        snap_before = fm.get_snapshot()
        fm.initialize(200_000.0)  # must be no-op
        snap_after = fm.get_snapshot()
        assert abs(snap_before.total - snap_after.total) < 0.01, (
            f"_total must not change on second initialize; "
            f"before={snap_before.total}, after={snap_after.total}"
        )
        assert abs(snap_after.total - 100_000.0) < 0.01
        warning_records = [
            r for r in handler.records
            if r.levelno == logging.WARNING
            and "initialize called again" in r.getMessage()
        ]
        assert len(warning_records) == 1, (
            f"expected exactly 1 WARNING on double-init; got {len(warning_records)}"
        )
        store.close()
    print("  OK H-4: second initialize() logs WARNING + leaves _total unchanged")


def test_h4_second_initialize_preserves_ledger_row_count() -> None:
    """Second initialize() must NOT write a second INIT fm_ledger row."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _make_fm(store)
        fm.initialize(100_000.0)
        rows_before = store.fetch_all(
            "SELECT * FROM fm_ledger WHERE entry_type = 'INIT'"
        )
        assert len(rows_before) == 1, "expected exactly 1 INIT row after first init"
        fm.initialize(200_000.0)  # must be no-op
        rows_after = store.fetch_all(
            "SELECT * FROM fm_ledger WHERE entry_type = 'INIT'"
        )
        assert len(rows_after) == 1, (
            f"expected still exactly 1 INIT row after second init; got {len(rows_after)}"
        )
        # Original row untouched (amount=100k, not 200k).
        assert abs(rows_after[0]["amount"] - 100_000.0) < 0.01
        store.close()
    print("  OK H-4: second initialize() does NOT pollute fm_ledger (row count stays 1)")


# ─────────────────────────────────────────────────────────────────────────────
# H-5 -- BEGIN IMMEDIATE + concurrent writers
# ─────────────────────────────────────────────────────────────────────────────

def test_h5_transaction_uses_begin_immediate() -> None:
    """Source-level guard: transaction() must use BEGIN IMMEDIATE, not plain BEGIN."""
    src = inspect.getsource(state_store_mod.StateStore.transaction)
    assert "BEGIN IMMEDIATE" in src, (
        "state_store.transaction() must use BEGIN IMMEDIATE (H-5)"
    )
    # Defensive: the bare 'cur.execute("BEGIN")' form must not reappear.
    assert 'cur.execute("BEGIN")' not in src, (
        "plain 'cur.execute(\"BEGIN\")' must not reappear in transaction() "
        "(H-5 regression)"
    )
    print("  OK H-5: state_store.transaction() uses BEGIN IMMEDIATE")


def test_h5_eight_concurrent_writers_no_locked_errors() -> None:
    """8 threads x 50 writes = 400 inserts, bounded at 5s, no sqlite locked errors.

    Uses mkdtemp + shutil.rmtree(ignore_errors=True) instead of
    TemporaryDirectory because StateStore.close() only closes the calling
    thread's connection; other threads' connections stay open and hold a
    file handle on Windows until GC'd.
    """
    tmp = tempfile.mkdtemp(prefix="e2_h5_stress_")
    try:
        store = StateStore(Path(tmp) / "stress.db")
        errors: list[tuple[int, str]] = []

        def writer(thread_id: int, count: int) -> None:
            try:
                for i in range(count):
                    with store.transaction() as cur:
                        cur.execute(
                            """
                            INSERT INTO signals
                              (signal_id, symbol, scanner, strategy,
                               triggered_at, received_at, expires_at,
                               status, fingerprint, fingerprint_date)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"sig-t{thread_id}-{i}", "RELIANCE",
                                "GAP_GO_LONG", "gap_go_long",
                                "2026-04-14T09:30:00+05:30",
                                "2026-04-14T09:30:01+05:30",
                                "2026-04-14T09:31:31+05:30",
                                "TRADED",
                                f"fp-t{thread_id}-{i}",
                                "2026-04-14",
                            ),
                        )
            except Exception as exc:
                errors.append((thread_id, f"{type(exc).__name__}: {exc}"))

        threads = [
            threading.Thread(target=writer, args=(tid, 50))
            for tid in range(8)
        ]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        elapsed = time.monotonic() - start
        assert not errors, f"concurrent writer errors: {errors}"
        assert elapsed < 5.0, f"8x50 writes exceeded 5s bound: {elapsed:.2f}s"
        count = store.row_count("signals")
        assert count == 400, f"expected 400 rows; got {count}"
        store.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"  OK H-5: 8x50 concurrent writes succeeded in {elapsed:.2f}s (bound 5s)")


# ─────────────────────────────────────────────────────────────────────────────
# drift_handler regression guard
# ─────────────────────────────────────────────────────────────────────────────

def test_escalating_sources_includes_fund_manager_bucket_overflow() -> None:
    """E.2 adds fund_manager_bucket_overflow to escalating-source frozenset."""
    assert "fund_manager_bucket_overflow" in drift_handler_mod._ESCALATING_SOURCES, (
        "drift_handler._ESCALATING_SOURCES must include "
        "'fund_manager_bucket_overflow' (H-1 / E.2)"
    )
    # Pre-existing sources still present (BL-2 + BL-3).
    assert "fund_manager" in drift_handler_mod._ESCALATING_SOURCES
    assert "fund_manager_self_check" in drift_handler_mod._ESCALATING_SOURCES
    print("  OK drift_handler._ESCALATING_SOURCES includes fund_manager_bucket_overflow + prior BL-2/BL-3 sources")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_h1_sync_bucket_overflow_raises,
        test_h1_sync_bucket_overflow_publishes_drift_with_correct_source_module,
        test_h1_sync_bucket_overflow_fires_hard_kill,
        test_h3_fund_manager_required_margin_matches_free_function,
        test_h3_order_placer_writes_trade_margin_from_required_margin_not_hardcode,
        test_h4_second_initialize_logs_warning_and_noop,
        test_h4_second_initialize_preserves_ledger_row_count,
        test_h5_transaction_uses_begin_immediate,
        test_h5_eight_concurrent_writers_no_locked_errors,
        test_escalating_sources_includes_fund_manager_bucket_overflow,
    ]
    print("=" * 70)
    print("test_e2_capital_tightening.py -- E.2 H-1/H-3/H-4/H-5 Test Suite")
    print("=" * 70)
    failed: list[tuple[str, str]] = []
    for t in tests:
        print(f"\n-> {t.__name__}")
        try:
            t()
        except Exception as exc:
            failed.append((t.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  FAIL: {exc}")
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)}")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
