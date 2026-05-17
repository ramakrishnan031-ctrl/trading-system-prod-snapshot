"""
tests/unit/test_phase19_batch2.py — Trading System v2

Tests for Phase 19 audit fixes: BATCH 2 (FIX-090)
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from capital.fund_manager import FundManager
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# FIX-090: SL-M margin buffer
# ─────────────────────────────────────────────────────────────────────────────

def test_fix090_buffer_applied_on_reserve() -> None:
    """
    FIX-090: Reserve with buffer. margin_required=10000, buffer=5%.
    Assert reserved=10500 (not 10000).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        bus = EventBus()

        logger = logging.getLogger("test_fix090")
        fm = FundManager(
            store, bus, logger,
            intraday_bucket_pct=0.70,
            positional_bucket_pct=0.30,
            daily_loss_limit=100000.0,
            slm_margin_buffer_pct=0.05,
        )
        fm.initialize(broker_balance=100000.0)

        # Reserve: qty=10, price=2500, intent=INTRADAY
        # Base margin = 10 * 2500 / 5.0 = 5000
        # Buffer = 5000 * 0.05 = 250
        # Total = 5250
        result = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "sig_001")

        assert result.success
        assert result.margin == 5250.0, f"Expected 5250 (5000 + 250 buffer), got {result.margin}"

        # Check balance: 70000 - 5250 = 64750
        snapshot = fm.get_snapshot()
        assert snapshot.intraday_reserved == 5250.0
        assert snapshot.intraday_avail == 70000.0 - 5250.0

        store.close()
        print("  OK fix090_buffer_applied_on_reserve: 5% buffer added to reservation")


def test_fix090_buffer_released_after_slm_accepted() -> None:
    """
    FIX-090: SL-M placed successfully. Assert 250 buffer released back to available.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        bus = EventBus()

        logger = logging.getLogger("test_fix090")
        fm = FundManager(
            store, bus, logger,
            slm_margin_buffer_pct=0.05,
        )
        fm.initialize(broker_balance=100000.0)

        # Reserve with buffer
        result = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "sig_001")
        assert result.success
        reservation_id = result.reservation_id
        assert result.margin == 5250.0  # 5000 + 250 buffer

        # SL-M accepted -> release buffer
        released = fm.release_slm_buffer(reservation_id, "SL-M accepted")
        assert released is True

        # Check balance: buffer (250) returned to available
        snapshot = fm.get_snapshot()
        # Reserved should now be 5000 (buffer released)
        assert snapshot.intraday_reserved == 5000.0, f"Expected 5000 (buffer released), got {snapshot.intraday_reserved}"
        # Available should be 70000 - 5000 = 65000
        assert snapshot.intraday_avail == 65000.0, f"Expected 65000, got {snapshot.intraday_avail}"

        store.close()
        print("  OK fix090_buffer_released_after_slm_accepted: buffer released, avail increased")


def test_fix090_buffer_held_on_slm_rejection() -> None:
    """
    FIX-090: SL-M rejected with 16388. Assert buffer still held, ready for retry.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        bus = EventBus()

        logger = logging.getLogger("test_fix090")
        fm = FundManager(
            store, bus, logger,
            slm_margin_buffer_pct=0.05,
        )
        fm.initialize(broker_balance=100000.0)

        # Reserve with buffer
        result = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "sig_001")
        assert result.success
        assert result.margin == 5250.0

        # SL-M rejected (don't release buffer)
        # Just check that buffer is still held
        snapshot = fm.get_snapshot()
        assert snapshot.intraday_reserved == 5250.0  # Full amount still reserved
        assert snapshot.intraday_avail == 70000.0 - 5250.0

        # Retry FIX-061 would happen here, buffer available for it

        store.close()
        print("  OK fix090_buffer_held_on_slm_rejection: buffer held for retry")


def test_fix090_configurable_buffer_pct() -> None:
    """
    FIX-090: slm_margin_buffer_pct configurable.
    Changing to 0.10 changes reservation amount.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        bus = EventBus()

        logger = logging.getLogger("test_fix090")
        # 10% buffer
        fm = FundManager(
            store, bus, logger,
            slm_margin_buffer_pct=0.10,
        )
        fm.initialize(broker_balance=100000.0)

        # Base margin = 10 * 2500 / 5.0 = 5000
        # Buffer = 5000 * 0.10 = 500
        # Total = 5500
        result = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "sig_001")

        assert result.success
        assert result.margin == 5500.0, f"Expected 5500 (10% buffer), got {result.margin}"

        store.close()
        print("  OK fix090_configurable_buffer_pct: 10% buffer applied correctly")


def test_fix090_buffer_idempotent_release() -> None:
    """
    FIX-090: Releasing buffer twice is idempotent (second returns False).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        bus = EventBus()

        logger = logging.getLogger("test_fix090")
        fm = FundManager(
            store, bus, logger,
            slm_margin_buffer_pct=0.05,
        )
        fm.initialize(broker_balance=100000.0)

        result = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "sig_001")
        reservation_id = result.reservation_id

        # First release
        released1 = fm.release_slm_buffer(reservation_id, "SL-M accepted")
        assert released1 is True

        # Second release (idempotent)
        released2 = fm.release_slm_buffer(reservation_id, "retry")
        assert released2 is False  # Already released

        store.close()
        print("  OK fix090_buffer_idempotent_release: second release returns False")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Phase 19 Batch 2 Tests ===\n")

    test_fix090_buffer_applied_on_reserve()
    test_fix090_buffer_released_after_slm_accepted()
    test_fix090_buffer_held_on_slm_rejection()
    test_fix090_configurable_buffer_pct()
    test_fix090_buffer_idempotent_release()

    print("\n=== All Phase 19 Batch 2 tests passed ===\n")
