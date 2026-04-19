"""
tests/unit/test_e5_cleanup.py

Phase E.5 tests: cleanup pass.

Covers:
  H-25  bind_trade adopted at 4+1 trade-scoped order_reconciler entry points.
        - Records emitted from inside the bound method carry trade_id extra.
        - The 5 target methods source-level reference `bind_trade(` as the
          binding call at entry (per-iteration for CHECK 8).
  H-10  is_active_for_dispatch removed from KillSwitch class (regression
        guard via hasattr).
  H-14  count_signals_today counts signal rows by received_at IST date;
        count_trades_today unchanged (regression guard).

Run: python -m pytest tests/unit/test_e5_cleanup.py -v
"""
from __future__ import annotations

import inspect
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.kill_switch import KillSwitch
from core.state_store import StateStore
from orders import order_reconciler as reconciler_mod


# ─────────────────────────────────────────────────────────────────────────────
# H-25: bind_trade adoption
# ─────────────────────────────────────────────────────────────────────────────

class _RecordingHandler(logging.Handler):
    """Capture log records for assertion on the bound extra fields."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_h25_bind_trade_attaches_trade_id_to_log_records() -> None:
    """
    H-25: a logger record emitted from _check5_position_grew carries the
    trade_id extra attribute because the method binds trade_id via
    bind_trade() at entry. _check5 is chosen because it is the smallest
    of the five methods and has no external dependencies beyond the bus
    and the logger.
    """
    rec = reconciler_mod.OrderReconciler.__new__(reconciler_mod.OrderReconciler)
    base_logger = logging.getLogger("test_h25_bind_trade")
    base_logger.setLevel(logging.DEBUG)
    base_logger.handlers.clear()
    handler = _RecordingHandler()
    base_logger.addHandler(handler)
    rec._log = base_logger
    rec._bus = MagicMock()

    trade = {"trade_id": "T-ALPHA-42", "symbol": "RELIANCE"}
    action = rec._check5_position_grew(trade, broker_qty=20, local_qty=10)

    assert action.check_name == "POSITION_GREW"
    assert any(
        getattr(r, "trade_id", None) == "T-ALPHA-42" for r in handler.records
    ), (
        "Expected at least one log record emitted from _check5 to carry "
        "trade_id='T-ALPHA-42' via bind_trade; got: "
        f"{[getattr(r, 'trade_id', None) for r in handler.records]!r}"
    )
    print("  OK H-25: _check5 emits records with bound trade_id")


def test_h25_reconciler_entry_points_use_bound_logger() -> None:
    """
    H-25: source-level regression guard. The 5 included methods must call
    bind_trade(...) at entry (inside the per-iteration loop for CHECK 8);
    the 6 excluded methods must NOT call bind_trade.
    """
    included = [
        reconciler_mod.OrderReconciler._check1_manual_close,
        reconciler_mod.OrderReconciler._check4_partial_close,
        reconciler_mod.OrderReconciler._check5_position_grew,
        reconciler_mod.OrderReconciler._g5b_crash_recovery_sl,
        reconciler_mod.OrderReconciler._check8_co_sl_drift,
    ]
    for fn in included:
        src = inspect.getsource(fn)
        assert "bind_trade(" in src, (
            f"{fn.__qualname__} must call bind_trade() at entry (H-25)"
        )

    excluded = [
        reconciler_mod.OrderReconciler._reconcile,
        reconciler_mod.OrderReconciler._g3_capital_drift,
        reconciler_mod.OrderReconciler._check7_capital_accounting_drift,
        reconciler_mod.OrderReconciler._poll_loop,
        reconciler_mod.OrderReconciler._note_auth_error,
        reconciler_mod.OrderReconciler._finalise_auth_counter,
    ]
    for fn in excluded:
        src = inspect.getsource(fn)
        assert "bind_trade(" not in src, (
            f"{fn.__qualname__} must NOT use bind_trade (H-25 scope boundary)"
        )
    print("  OK H-25: 5 included methods use bind_trade; 6 excluded do not")


# ─────────────────────────────────────────────────────────────────────────────
# H-10: is_active_for_dispatch removed
# ─────────────────────────────────────────────────────────────────────────────

def test_h10_is_active_for_dispatch_removed_from_class() -> None:
    """
    H-10: is_active_for_dispatch was a vestigial helper with zero production
    callers. Regression guard: the attribute must not exist on KillSwitch
    (class or instance).
    """
    assert not hasattr(KillSwitch, "is_active_for_dispatch"), (
        "KillSwitch.is_active_for_dispatch must be deleted (H-10)"
    )
    print("  OK H-10: KillSwitch.is_active_for_dispatch removed")


# ─────────────────────────────────────────────────────────────────────────────
# H-14: count_signals_today
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> StateStore:
    db = tmp_path / "e5.db"
    store = StateStore(str(db))
    return store


def _insert_signal(
    store: StateStore,
    *,
    signal_id: str,
    received_at: str,
    triggered_at: str | None = None,
) -> None:
    triggered_at = triggered_at or received_at
    fingerprint = f"fp-{signal_id}"
    fingerprint_date = received_at[:10]
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, rejection_reason, trade_id, trigger_price,
               fingerprint, fingerprint_date)
            VALUES (?, 'RELIANCE', 'chartink', 'strat_a', ?, ?, ?, 'DROPPED_DEDUP',
                    NULL, NULL, NULL, ?, ?)
            """,
            (signal_id, triggered_at, received_at, received_at,
             fingerprint, fingerprint_date),
        )


def test_h14_count_signals_today_counts_signals(tmp_path: Path) -> None:
    """
    H-14: count_signals_today returns the number of signal rows whose
    received_at has the given YYYY-MM-DD prefix. Includes signals that
    never became trades.
    """
    store = _make_store(tmp_path)
    try:
        today = "2026-04-20"
        yesterday = "2026-04-19"

        _insert_signal(store, signal_id="s1",
                       received_at=f"{today}T09:15:00+05:30")
        _insert_signal(store, signal_id="s2",
                       received_at=f"{today}T10:30:45+05:30")
        _insert_signal(store, signal_id="s3",
                       received_at=f"{today}T14:59:00+05:30")
        _insert_signal(store, signal_id="s4",
                       received_at=f"{yesterday}T11:00:00+05:30")

        assert store.count_signals_today(today) == 3
        assert store.count_signals_today(yesterday) == 1
        assert store.count_signals_today("2026-04-18") == 0
        print("  OK H-14: count_signals_today counts by received_at date")
    finally:
        store.close()


def test_h14_count_trades_today_unchanged(tmp_path: Path) -> None:
    """
    H-14: regression guard — count_trades_today behaviour is unchanged by
    the H-14 addition. Signal inserts do NOT affect the trades count.
    """
    store = _make_store(tmp_path)
    try:
        today = "2026-04-20"
        _insert_signal(store, signal_id="s1",
                       received_at=f"{today}T09:15:00+05:30")
        assert store.count_trades_today(today) == 0
        print("  OK H-14: count_trades_today unchanged by signal inserts")
    finally:
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    passed = 0
    failed = 0
    tests = [
        test_h25_bind_trade_attaches_trade_id_to_log_records,
        test_h25_reconciler_entry_points_use_bound_logger,
        test_h10_is_active_for_dispatch_removed_from_class,
    ]
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:  # pragma: no cover - runner convenience
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")

    tmp_tests = [
        test_h14_count_signals_today_counts_signals,
        test_h14_count_trades_today_unchanged,
    ]
    for t in tmp_tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                t(Path(tmp))
                passed += 1
            except Exception as exc:  # pragma: no cover
                failed += 1
                print(f"  FAIL {t.__name__}: {exc}")

    print(f"\n  E.5 cleanup: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
