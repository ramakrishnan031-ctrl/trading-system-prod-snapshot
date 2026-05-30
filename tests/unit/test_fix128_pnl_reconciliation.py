"""
tests/unit/test_fix128_pnl_reconciliation.py

Tests for FIX-128 Fix B: P&L reconciliation.
  - StateStore helpers: get_today_closed_pnl, upsert_pnl_reconciliation, get_pnl_reconciliation
  - reconcile_pnl.run_reconciliation(): paper mode, within limit, variance minor, variance major
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from core.time_authority import now_ist, today_ist
from scripts.reconcile_pnl import run_reconciliation


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> StateStore:
    db = tmp_path / "test.db"
    return StateStore(db_path=db)


def _log() -> logging.Logger:
    return logging.getLogger("test_fix128_pnl_recon")


def _insert_closed_trade(store: StateStore, net_pnl: float, date_iso: str) -> None:
    """Insert a minimal closed trade row with given net_pnl."""
    from core.ids import new_signal_id, new_trade_id
    sig_id = new_signal_id()
    trade_id = new_trade_id()
    now_str = f"{date_iso}T15:30:00+05:30"

    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy,
               triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, 'RELIANCE', 'test', 'test', ?, ?, ?, 'TRADED', ?, ?)""",
            (sig_id, now_str, now_str, now_str, sig_id[:8], date_iso),
        )
        cur.execute(
            """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
               status, qty_planned, entry_target_price, sl_initial, tgt_initial,
               order_protocol, margin_reserved, risk_amount, net_pnl, created_at, updated_at)
               VALUES (?, ?, 'RELIANCE', 'LONG', 'test',
               'CLOSED', 10, 1000.0, 950.0, 1100.0, 'LIMIT_TRIPLE', 2000.0, 500.0,
               ?, ?, ?)""",
            (trade_id, sig_id, net_pnl, now_str, now_str),
        )


# ─────────────────────────────────────────────────────────────────────────────
# StateStore helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestStateStorePnlHelpers:

    def test_get_today_closed_pnl_no_trades_returns_zero(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            result = store.get_today_closed_pnl("2026-05-30")
            assert result == 0.0
            store.close()
            print("  OK get_today_closed_pnl: no trades → 0.0")

    def test_get_today_closed_pnl_sums_closed_trades(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _insert_closed_trade(store, 500.0, "2026-05-30")
            _insert_closed_trade(store, -200.0, "2026-05-30")
            result = store.get_today_closed_pnl("2026-05-30")
            assert abs(result - 300.0) < 0.01, f"Expected 300.0, got {result}"
            store.close()
            print("  OK get_today_closed_pnl: sums closed trade net_pnl")

    def test_get_today_closed_pnl_excludes_other_dates(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _insert_closed_trade(store, 1000.0, "2026-05-29")  # yesterday
            result = store.get_today_closed_pnl("2026-05-30")
            assert result == 0.0, "Should not include trades from other dates"
            store.close()
            print("  OK get_today_closed_pnl: excludes other-date trades")

    def test_upsert_pnl_reconciliation_insert_and_fetch(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            store.upsert_pnl_reconciliation(
                date="2026-05-30",
                broker_pnl=1500.0,
                system_pnl=1495.0,
                variance=5.0,
                status="OK",
                notes=None,
                created_at=now_ist().isoformat(),
            )
            row = store.get_pnl_reconciliation("2026-05-30")
            assert row is not None
            assert row["status"] == "OK"
            assert abs(row["broker_pnl"] - 1500.0) < 0.01
            assert abs(row["system_pnl"] - 1495.0) < 0.01
            assert abs(row["variance"] - 5.0) < 0.01
            store.close()
            print("  OK upsert + get pnl_reconciliation round-trip")

    def test_upsert_pnl_reconciliation_idempotent(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            store.upsert_pnl_reconciliation(
                date="2026-05-30",
                broker_pnl=1000.0, system_pnl=950.0, variance=50.0,
                status="VARIANCE_MINOR", notes="first run",
                created_at=now_ist().isoformat(),
            )
            # Second upsert replaces first
            store.upsert_pnl_reconciliation(
                date="2026-05-30",
                broker_pnl=1010.0, system_pnl=950.0, variance=60.0,
                status="VARIANCE_MINOR", notes="second run",
                created_at=now_ist().isoformat(),
            )
            row = store.get_pnl_reconciliation("2026-05-30")
            assert abs(row["broker_pnl"] - 1010.0) < 0.01, "Should reflect second upsert"
            store.close()
            print("  OK upsert_pnl_reconciliation idempotent (second run overwrites)")

    def test_get_pnl_reconciliation_missing_returns_none(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            row = store.get_pnl_reconciliation("2099-01-01")
            assert row is None
            store.close()
            print("  OK get_pnl_reconciliation: missing date → None")


# ─────────────────────────────────────────────────────────────────────────────
# run_reconciliation()
# ─────────────────────────────────────────────────────────────────────────────

class TestRunReconciliation:

    def test_paper_mode_skips_broker_fetch(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            result = run_reconciliation(
                store=store, date_iso="2026-05-30", is_paper=True,
                log=_log(), notifier=None, kill_switch=None, dry_run=True,
            )
            assert result["status"] == "PAPER_SKIPPED"
            assert result["broker_pnl"] is None
            assert result["variance"] is None
            store.close()
            print("  OK paper mode: PAPER_SKIPPED status, no broker fetch")

    def test_paper_mode_persists_to_db(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _insert_closed_trade(store, 750.0, "2026-05-30")
            run_reconciliation(
                store=store, date_iso="2026-05-30", is_paper=True,
                log=_log(), notifier=None, kill_switch=None, dry_run=False,
            )
            row = store.get_pnl_reconciliation("2026-05-30")
            assert row is not None
            assert row["status"] == "PAPER_SKIPPED"
            assert abs(row["system_pnl"] - 750.0) < 0.01
            store.close()
            print("  OK paper mode: writes PAPER_SKIPPED row with correct system_pnl")

    def test_variance_within_limit_no_alert(self) -> None:
        """Variance <= Rs 10 → OK status, no Telegram, no kill_switch."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _insert_closed_trade(store, 1000.0, "2026-05-30")
            notifier = MagicMock()
            kill_switch = MagicMock()

            # Inject broker_pnl via monkeypatching _fetch_broker_day_pnl
            import scripts.reconcile_pnl as mod
            original_fetch = mod._fetch_broker_day_pnl
            mod._fetch_broker_day_pnl = lambda log: 1005.0  # variance = 5.0

            try:
                result = run_reconciliation(
                    store=store, date_iso="2026-05-30", is_paper=False,
                    log=_log(), notifier=notifier, kill_switch=kill_switch, dry_run=True,
                )
            finally:
                mod._fetch_broker_day_pnl = original_fetch

            assert result["status"] == "OK"
            assert abs(result["variance"] - 5.0) < 0.01
            assert not notifier.send.called, "No alert for variance <= Rs 10"
            assert not kill_switch.soft_kill.called
            store.close()
            print("  OK variance Rs 5 <= Rs 10 → status=OK, no alert")

    def test_variance_minor_sends_alert(self) -> None:
        """Variance > Rs 10 and <= Rs 100 → VARIANCE_MINOR, WARNING alert."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _insert_closed_trade(store, 1000.0, "2026-05-30")
            notifier = MagicMock()
            kill_switch = MagicMock()

            import scripts.reconcile_pnl as mod
            original_fetch = mod._fetch_broker_day_pnl
            mod._fetch_broker_day_pnl = lambda log: 1050.0  # variance = 50.0

            try:
                result = run_reconciliation(
                    store=store, date_iso="2026-05-30", is_paper=False,
                    log=_log(), notifier=notifier, kill_switch=kill_switch, dry_run=True,
                )
            finally:
                mod._fetch_broker_day_pnl = original_fetch

            assert result["status"] == "VARIANCE_MINOR"
            assert abs(result["variance"] - 50.0) < 0.01
            assert notifier.send.called, "Telegram alert expected for VARIANCE_MINOR"
            assert not kill_switch.soft_kill.called, "No soft_kill for VARIANCE_MINOR"
            store.close()
            print("  OK variance Rs 50 → VARIANCE_MINOR, alert sent, no soft_kill")

    def test_variance_major_triggers_soft_kill(self) -> None:
        """Variance > Rs 100 → VARIANCE_MAJOR, alert + SOFT_KILL."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _insert_closed_trade(store, 1000.0, "2026-05-30")
            notifier = MagicMock()
            kill_switch = MagicMock()

            import scripts.reconcile_pnl as mod
            original_fetch = mod._fetch_broker_day_pnl
            mod._fetch_broker_day_pnl = lambda log: 1500.0  # variance = 500.0

            try:
                result = run_reconciliation(
                    store=store, date_iso="2026-05-30", is_paper=False,
                    log=_log(), notifier=notifier, kill_switch=kill_switch, dry_run=False,
                )
            finally:
                mod._fetch_broker_day_pnl = original_fetch

            assert result["status"] == "VARIANCE_MAJOR"
            assert result["variance"] > 100.0
            assert notifier.send.called
            assert kill_switch.soft_kill.called, "soft_kill must fire for VARIANCE_MAJOR"
            # DB row written
            row = store.get_pnl_reconciliation("2026-05-30")
            assert row is not None
            assert row["status"] == "VARIANCE_MAJOR"
            store.close()
            print("  OK variance Rs 500 → VARIANCE_MAJOR, alert + soft_kill triggered")

    def test_broker_fetch_error_stores_error_status(self) -> None:
        """Broker fetch failure → status=ERROR, no kill_switch."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            notifier = MagicMock()
            kill_switch = MagicMock()

            import scripts.reconcile_pnl as mod
            original_fetch = mod._fetch_broker_day_pnl
            mod._fetch_broker_day_pnl = lambda log: (_ for _ in ()).throw(
                RuntimeError("network error")
            )

            try:
                result = run_reconciliation(
                    store=store, date_iso="2026-05-30", is_paper=False,
                    log=_log(), notifier=notifier, kill_switch=kill_switch, dry_run=False,
                )
            finally:
                mod._fetch_broker_day_pnl = original_fetch

            assert result["status"] == "ERROR"
            assert result["broker_pnl"] is None
            assert not kill_switch.soft_kill.called
            row = store.get_pnl_reconciliation("2026-05-30")
            assert row is not None
            assert row["status"] == "ERROR"
            store.close()
            print("  OK broker fetch failure → ERROR status stored, no soft_kill")

    def test_dry_run_does_not_write_db(self) -> None:
        """dry_run=True: reconcile runs but does not write to DB."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            run_reconciliation(
                store=store, date_iso="2026-05-30", is_paper=True,
                log=_log(), notifier=None, kill_switch=None, dry_run=True,
            )
            row = store.get_pnl_reconciliation("2026-05-30")
            assert row is None, "dry_run should not write to DB"
            store.close()
            print("  OK dry_run=True: no DB write")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        TestStateStorePnlHelpers().test_get_today_closed_pnl_no_trades_returns_zero,
        TestStateStorePnlHelpers().test_get_today_closed_pnl_sums_closed_trades,
        TestStateStorePnlHelpers().test_get_today_closed_pnl_excludes_other_dates,
        TestStateStorePnlHelpers().test_upsert_pnl_reconciliation_insert_and_fetch,
        TestStateStorePnlHelpers().test_upsert_pnl_reconciliation_idempotent,
        TestStateStorePnlHelpers().test_get_pnl_reconciliation_missing_returns_none,
        TestRunReconciliation().test_paper_mode_skips_broker_fetch,
        TestRunReconciliation().test_paper_mode_persists_to_db,
        TestRunReconciliation().test_variance_within_limit_no_alert,
        TestRunReconciliation().test_variance_minor_sends_alert,
        TestRunReconciliation().test_variance_major_triggers_soft_kill,
        TestRunReconciliation().test_broker_fetch_error_stores_error_status,
        TestRunReconciliation().test_dry_run_does_not_write_db,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
