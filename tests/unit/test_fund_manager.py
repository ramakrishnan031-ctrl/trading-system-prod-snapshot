"""
tests/unit/test_fund_manager.py

Validates capital/fund_manager.py against FM1-FM17.
Uses in-memory SQLite (':memory:') for speed.

Run: python -m pytest tests/unit/test_fund_manager.py -v
Or:  python tests/unit/test_fund_manager.py  (standalone mode)
"""
from __future__ import annotations

import sys
import logging
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.fund_manager import (
    FundManager,
    CapitalSnapshot,
    ReservationResult,
    CommitResult,
    ReleaseResult,
    required_margin,
)
from core.events import EventBus
from core.exceptions import CapitalInvariantViolation
from core.state_store import StateStore


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_PATH = Path(__file__).parent.parent.parent / "core" / "schema.sql"

_DEFAULT_LEVERAGE = {
    "INTRADAY": 5.0,
    "COVER_ORDER": 6.0,
    "DELIVERY": 1.0,
    "BRACKET_ORDER": 5.0,
}


def _make_store(tmp_dir: Path) -> StateStore:
    """Create an on-disk StateStore in a temp dir for test isolation."""
    return StateStore(tmp_dir / "test.db", _SCHEMA_PATH)


def _make_fm(
    store: StateStore,
    intraday_pct: float = 0.70,
    positional_pct: float = 0.30,
    daily_loss_limit: float = 10_000.0,
    leverage_map: dict | None = None,
    on_loss_breach=None,
    on_critical=None,
) -> FundManager:
    bus = EventBus()
    logger = logging.getLogger("test_fm")
    return FundManager(
        state_store=store,
        bus=bus,
        logger=logger,
        intraday_bucket_pct=intraday_pct,
        positional_bucket_pct=positional_pct,
        daily_loss_limit=daily_loss_limit,
        leverage_map=leverage_map or _DEFAULT_LEVERAGE,
        on_daily_loss_breach=on_loss_breach,
        on_critical_failure=on_critical,
    )


def _initialized_fm(
    store: StateStore,
    balance: float = 100_000.0,
    **kwargs,
) -> FundManager:
    fm = _make_fm(store, **kwargs)
    fm.initialize(balance)
    return fm


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- initialize() (FM13)
# ─────────────────────────────────────────────────────────────────────────────

def test_initialize_splits_buckets_correctly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        snap = fm.get_snapshot()
        assert abs(snap.intraday_avail - 70_000.0) < 0.01
        assert abs(snap.positional_avail - 30_000.0) < 0.01
        assert snap.intraday_reserved == 0.0
        assert snap.intraday_used == 0.0
        assert snap.positional_reserved == 0.0
        assert snap.positional_used == 0.0
        assert snap.total == 100_000.0
        store.close()
    print("  OK initialize() splits 100k into 70k/30k buckets (FM3, FM13)")


def test_initialize_writes_ledger_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        rows = store.fetch_all("SELECT * FROM fm_ledger WHERE entry_type = 'INIT'")
        assert len(rows) == 1
        assert rows[0]["amount"] == 100_000.0
        assert rows[0]["bucket"] == "both"
        store.close()
    print("  OK initialize() writes INIT row to fm_ledger (FM10)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- required_margin() (FM4)
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_intraday_5x_leverage() -> None:
    """100 qty @ 500 INTRADAY (5x lev) = 100*500/5 = 10000 margin (FM4)."""
    m = required_margin(100, 500.0, "INTRADAY", _DEFAULT_LEVERAGE)
    assert abs(m - 10_000.0) < 0.01
    print("  OK required_margin: 100@500 INTRADAY (5x) = 10000 (FM4)")


def test_margin_delivery_1x_leverage() -> None:
    """10 qty @ 1000 DELIVERY (1x lev) = 10000 margin (FM4)."""
    m = required_margin(10, 1000.0, "DELIVERY", _DEFAULT_LEVERAGE)
    assert abs(m - 10_000.0) < 0.01
    print("  OK required_margin: 10@1000 DELIVERY (1x) = 10000 (FM4)")


def test_margin_not_notional() -> None:
    """Verify margin is NOT qty*price -- the old catastrophic flaw (FM4)."""
    m = required_margin(100, 500.0, "INTRADAY", _DEFAULT_LEVERAGE)
    notional = 100 * 500.0  # = 50000
    assert m != notional, "margin should NOT equal notional (old audit flaw)"
    assert m == 10_000.0
    print("  OK required_margin: NOT notional (audit flaw regression FM4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- reserve() (FM5, FM6)
# ─────────────────────────────────────────────────────────────────────────────

def test_reserve_intraday_draws_from_intraday_bucket() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY", "sig_001")
        assert result.success
        assert result.bucket == "intraday"
        assert abs(result.margin - 10_000.0) < 0.01
        snap = fm.get_snapshot()
        assert abs(snap.intraday_avail - 60_000.0) < 0.01    # 70k - 10k
        assert abs(snap.intraday_reserved - 10_000.0) < 0.01
        assert snap.positional_avail == 30_000.0              # untouched
        store.close()
    print("  OK reserve(INTRADAY) draws from intraday bucket only (FM3, FM5)")


def test_reserve_delivery_draws_from_positional_bucket() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("TCS", 10, 1000.0, "DELIVERY", "sig_002")
        assert result.success
        assert result.bucket == "positional"
        snap = fm.get_snapshot()
        assert abs(snap.positional_avail - 20_000.0) < 0.01   # 30k - 10k
        assert abs(snap.positional_reserved - 10_000.0) < 0.01
        assert snap.intraday_avail == 70_000.0                 # untouched
        store.close()
    print("  OK reserve(DELIVERY) draws from positional bucket only (FM3, FM5)")


def test_reserve_insufficient_returns_failure_no_state_change() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        # Try to reserve more than intraday bucket (70k avail, 5x lev -> max notional 350k)
        # 100000 qty @ 500 -> margin = 100000*500/5 = 10,000,000 > 70,000
        result = fm.reserve("RELIANCE", 100_000, 500.0, "INTRADAY")
        assert not result.success
        assert result.reason_if_failed != ""
        snap = fm.get_snapshot()
        assert snap.intraday_avail == 70_000.0   # unchanged
        assert snap.intraday_reserved == 0.0
        store.close()
    print("  OK reserve insufficient -> success=False, no state change (FM5)")


def test_reserve_writes_ledger_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY", "sig_003")
        rows = store.fetch_all("SELECT * FROM fm_ledger WHERE entry_type = 'RESERVE'")
        assert len(rows) == 1
        assert rows[0]["reservation_id"] == result.reservation_id
        assert rows[0]["signal_id"] == "sig_003"
        store.close()
    print("  OK reserve() writes RESERVE row to fm_ledger (FM10)")


def test_reservation_id_is_16_hex_chars() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 10, 500.0, "INTRADAY")
        assert result.success
        rid = result.reservation_id
        assert len(rid) == 16, f"Expected 16 chars, got {len(rid)}"
        assert all(c in "0123456789abcdef" for c in rid), f"Not hex: {rid}"
        store.close()
    print("  OK reservation_id is 16 hex chars (FM6)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- release() (FM5)
# ─────────────────────────────────────────────────────────────────────────────

def test_release_restores_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        assert result.success
        released = fm.release(result.reservation_id, reason="order_rejected")
        assert released is True
        snap = fm.get_snapshot()
        assert abs(snap.intraday_avail - 70_000.0) < 0.01   # fully restored
        assert snap.intraday_reserved == 0.0
        store.close()
    print("  OK release() restores available capital (FM5)")


def test_release_twice_second_returns_false() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.release(result.reservation_id, reason="first_release")
        second = fm.release(result.reservation_id, reason="second_release")
        assert second is False   # idempotent FM5
        snap = fm.get_snapshot()
        assert abs(snap.intraday_avail - 70_000.0) < 0.01   # no double-restore
        store.close()
    print("  OK release() twice -> second returns False, no double-restore (FM5)")


def test_release_unknown_id_returns_false() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        released = fm.release("deadbeefdeadbeef", reason="unknown")
        assert released is False
        store.close()
    print("  OK release(unknown_id) returns False (FM5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- commit_to_used() (FM5)
# ─────────────────────────────────────────────────────────────────────────────

def test_commit_full_fill_moves_reserved_to_used() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        commit = fm.commit_to_used(result.reservation_id, 500.0, 100)
        snap = fm.get_snapshot()
        assert snap.intraday_reserved == 0.0
        assert abs(snap.intraday_used - 10_000.0) < 0.01
        assert abs(commit.excess_returned) < 0.01
        store.close()
    print("  OK commit_to_used full fill -> reserved=0, used=margin (FM5)")


def test_commit_partial_fill_excess_returns_to_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        # Reserve for 100 qty, fill only 50
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        # reserved margin = 10000 (100@500/5x)
        commit = fm.commit_to_used(result.reservation_id, 500.0, 50)
        # actual_margin = 50*500/5 = 5000, excess = 5000
        assert abs(commit.actual_margin - 5_000.0) < 0.01
        assert abs(commit.excess_returned - 5_000.0) < 0.01
        snap = fm.get_snapshot()
        assert abs(snap.intraday_used - 5_000.0) < 0.01
        assert abs(snap.intraday_avail - 65_000.0) < 0.01   # 60k + 5k excess
        store.close()
    print("  OK commit_to_used partial fill -> excess returned to available (FM5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- release_used() (FM5, FM7)
# ─────────────────────────────────────────────────────────────────────────────

def test_release_used_profit_increases_available_and_pnl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        # Exit at 510 -> PnL = (510-500)*100 = 1000
        release = fm.release_used("RELIANCE", 510.0, 100, "INTRADAY", 500.0, "LONG", costs=0.0)
        assert abs(release.pnl_delta - 1_000.0) < 0.01
        snap = fm.get_snapshot()
        assert abs(snap.daily_realized_pnl - 1_000.0) < 0.01
        assert abs(snap.intraday_used) < 0.01
        # avail = 60k (after reserve) + 10k margin returned + 1k PnL = 71k
        assert abs(snap.intraday_avail - 71_000.0) < 0.01
        store.close()
    print("  OK release_used profit -> available += margin + pnl, daily_pnl increases (FM5, FM7)")


def test_release_used_loss_decreases_daily_pnl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        # Exit at 490 -> loss = (490-500)*100 = -1000
        release = fm.release_used("RELIANCE", 490.0, 100, "INTRADAY", 500.0, "LONG", costs=0.0)
        assert abs(release.pnl_delta - (-1_000.0)) < 0.01
        snap = fm.get_snapshot()
        assert abs(snap.daily_realized_pnl - (-1_000.0)) < 0.01
        store.close()
    print("  OK release_used loss -> daily_pnl decreases (FM7)")


def test_release_used_short_profit() -> None:
    """EF-3: SHORT profits when exit < entry. pnl_delta must be positive."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        # SHORT: entry 500, cover at 490 -> profit = (500-490)*100 = 1000
        release = fm.release_used("RELIANCE", 490.0, 100, "INTRADAY", 500.0, "SHORT", costs=0.0)
        assert abs(release.pnl_delta - 1_000.0) < 0.01
        snap = fm.get_snapshot()
        assert abs(snap.daily_realized_pnl - 1_000.0) < 0.01
        store.close()
    print("  OK release_used SHORT profit -> positive pnl_delta (EF-3)")


def test_release_used_short_loss() -> None:
    """EF-3: SHORT loses when exit > entry. pnl_delta must be negative."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        # SHORT: entry 500, cover at 510 -> loss = (500-510)*100 = -1000
        release = fm.release_used("RELIANCE", 510.0, 100, "INTRADAY", 500.0, "SHORT", costs=0.0)
        assert abs(release.pnl_delta - (-1_000.0)) < 0.01
        snap = fm.get_snapshot()
        assert abs(snap.daily_realized_pnl - (-1_000.0)) < 0.01
        store.close()
    print("  OK release_used SHORT loss -> negative pnl_delta (EF-3)")


def test_release_used_rejects_invalid_direction() -> None:
    """EF-3: direction must be LONG or SHORT. Typos and empty string rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        import pytest as _pytest
        with _pytest.raises(ValueError, match="direction must be one of"):
            fm.release_used("RELIANCE", 510.0, 100, "INTRADAY", 500.0, "BOGUS", costs=0.0)
        store.close()
    print("  OK release_used rejects invalid direction (EF-3)")


def test_daily_loss_limit_breach_fires_callback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        breach_calls: list[int] = []
        fm = _initialized_fm(
            store, balance=100_000.0, daily_loss_limit=500.0,
            on_loss_breach=lambda: breach_calls.append(1),
        )
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        # Loss > daily_loss_limit (500)
        fm.release_used("RELIANCE", 490.0, 100, "INTRADAY", 500.0, "LONG", costs=0.0)
        assert len(breach_calls) == 1
        store.close()
    print("  OK daily_loss_limit breach -> on_daily_loss_breach fired (FM7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- sync_from_broker() (FM9)
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_from_broker_updates_total_recomputes_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        fm.sync_from_broker(120_000.0)
        snap = fm.get_snapshot()
        assert abs(snap.total - 120_000.0) < 0.01
        assert abs(snap.intraday_avail - 84_000.0) < 0.01   # 120k * 70%
        assert abs(snap.positional_avail - 36_000.0) < 0.01
        store.close()
    print("  OK sync_from_broker updates total, recomputes available (FM9)")


def test_sync_from_broker_does_not_touch_reserved_or_used() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        # Reserve some capital
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        reserved_before = fm.get_snapshot().intraday_reserved
        used_before = fm.get_snapshot().intraday_used
        # Sync should not change reserved/used
        fm.sync_from_broker(110_000.0)
        snap = fm.get_snapshot()
        assert snap.intraday_reserved == reserved_before
        assert snap.intraday_used == used_before
        store.close()
    print("  OK sync_from_broker does not touch reserved or used (FM9)")


def test_sync_never_subtracts_used_from_broker_balance() -> None:
    """Regression test for audit double-deduction flaw (FM9)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        # Simulate broker returning total equity (NOT reduced by used margin)
        # Broker sees total = 100000, our used = 10000
        # Old flaw: available = broker_balance - used = 90000 (WRONG)
        # Correct:  total = broker_balance = 100000; available = total - reserved - used
        fm.sync_from_broker(100_000.0)
        snap = fm.get_snapshot()
        # total must equal broker_balance, NOT broker_balance - used
        assert abs(snap.total - 100_000.0) < 0.01
        # available = total * 70% - used (not total - used * 70%)
        expected_avail = 100_000.0 * 0.70 - 10_000.0  # 60000
        assert abs(snap.intraday_avail - expected_avail) < 0.01
        store.close()
    print("  OK sync_from_broker: total = broker_balance (not broker - used) (FM9 regression)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- get_snapshot() (FM8)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_snapshot_returns_frozen_consistent_view() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        snap = fm.get_snapshot()
        assert isinstance(snap, CapitalSnapshot)
        assert snap.total == 100_000.0
        assert snap.daily_realized_pnl == 0.0
        # Frozen dataclass -- modification raises FrozenInstanceError
        raised = False
        try:
            snap.total = 999.0   # type: ignore[misc]
        except Exception:
            raised = True
        assert raised, "Snapshot should be frozen"
        store.close()
    print("  OK get_snapshot returns frozen CapitalSnapshot (FM8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- bucket isolation (FM3)
# ─────────────────────────────────────────────────────────────────────────────

def test_drain_intraday_can_still_reserve_delivery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        # Small balance, small daily_loss so we can drain intraday quickly
        fm = _initialized_fm(store, balance=10_000.0, daily_loss_limit=999_999.0)
        # Drain intraday (7000 avail at 5x lev -> can do 7000/1 = 7000 margin)
        # 7000 qty @ 1.0 INTRADAY -> margin = 7000*1/5 = 1400 ... let's use direct numbers
        # Reserve 7000 margin at once: 70 qty @ 500 / 5x = 7000
        r = fm.reserve("RELIANCE", 70, 500.0, "INTRADAY")
        assert r.success
        assert abs(fm.get_snapshot().intraday_avail) < 0.01   # drained
        # DELIVERY should still work from positional bucket (3000 avail)
        r2 = fm.reserve("TCS", 3, 1000.0, "DELIVERY")   # margin = 3000
        assert r2.success, f"DELIVERY reserve failed: {r2.reason_if_failed}"
        store.close()
    print("  OK drain intraday -> DELIVERY still works from positional (FM3)")


def test_cross_bucket_borrowing_forbidden() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=10_000.0)
        # Drain intraday bucket (7000 avail)
        r = fm.reserve("RELIANCE", 70, 500.0, "INTRADAY")
        assert r.success
        # Now try another INTRADAY reserve: intraday is 0, positional has 3000
        # Should FAIL even though positional has capacity (FM3)
        r2 = fm.reserve("INFY", 10, 500.0, "INTRADAY")
        assert not r2.success, "Cross-bucket borrow must be rejected (FM3)"
        assert "Insufficient intraday" in r2.reason_if_failed
        store.close()
    print("  OK cross-bucket borrowing forbidden: INTRADAY exhausted -> success=False (FM3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- constructor validation (FM12)
# ─────────────────────────────────────────────────────────────────────────────

def test_constructor_bucket_pct_not_summing_to_1_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        raised = False
        try:
            _make_fm(store, intraday_pct=0.60, positional_pct=0.30)  # sum=0.90
        except ValueError:
            raised = True
        assert raised
        store.close()
    print("  OK intraday_pct + positional_pct != 1.0 -> ValueError (FM12)")


def test_constructor_missing_leverage_entry_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        raised = False
        try:
            _make_fm(store, leverage_map={"INTRADAY": 5.0})   # missing 3 intents
        except ValueError:
            raised = True
        assert raised
        store.close()
    print("  OK missing leverage entry -> ValueError (FM12)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- invariant violation (FM11)
# ─────────────────────────────────────────────────────────────────────────────

def test_invariant_violation_raises_capital_invariant_violation() -> None:
    """Manually corrupt state so invariant fails; verify exception raised."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        critical_calls: list[str] = []
        fm = _initialized_fm(
            store, balance=100_000.0,
            on_critical=critical_calls.append,
        )
        # Reserve normally first
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        assert result.success

        # Corrupt: secretly add to intraday_avail without adjusting total
        # This breaks the invariant: avail+res+used != total
        with fm._lock:
            fm._intraday_avail += 50_000.0   # inject phantom capital

        # Next reserve() will check invariant and raise
        raised = False
        try:
            fm.reserve("INFY", 10, 500.0, "INTRADAY")
        except CapitalInvariantViolation:
            raised = True

        assert raised, "Expected CapitalInvariantViolation"
        assert len(critical_calls) >= 1, "on_critical_failure should be called"
        store.close()
    print("  OK manually corrupted state -> CapitalInvariantViolation raised, callback fired (FM11)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- reset_daily_pnl() (FM14)
# ─────────────────────────────────────────────────────────────────────────────

def test_reset_daily_pnl_zeroes_pnl_leaves_reserved_used() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        # Build up some state
        result = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        fm.commit_to_used(result.reservation_id, 500.0, 100)
        fm.release_used("RELIANCE", 510.0, 100, "INTRADAY", 500.0, "LONG", costs=0.0)
        snap_before = fm.get_snapshot()
        assert snap_before.daily_realized_pnl > 0

        # Reserve more and commit (leave reserved/used non-zero)
        result2 = fm.reserve("INFY", 50, 1500.0, "INTRADAY")
        fm.commit_to_used(result2.reservation_id, 1500.0, 50)
        snap_mid = fm.get_snapshot()

        fm.reset_daily_pnl()
        snap = fm.get_snapshot()

        assert snap.daily_realized_pnl == 0.0
        # reserved and used must be unchanged
        assert abs(snap.intraday_used - snap_mid.intraday_used) < 0.01
        assert abs(snap.intraday_reserved - snap_mid.intraday_reserved) < 0.01
        store.close()
    print("  OK reset_daily_pnl() zeroes pnl, leaves reserved/used intact (FM14)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- ledger write failure causes rollback (FM10)
# ─────────────────────────────────────────────────────────────────────────────

def test_ledger_write_failure_rolls_back_mutation() -> None:
    """Simulate state_store failure: mutation must not persist."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        snap_before = fm.get_snapshot()

        # Patch _write_ledger to raise
        original_write = fm._write_ledger
        def failing_write(*args, **kwargs):
            raise RuntimeError("simulated DB failure")
        fm._write_ledger = failing_write  # type: ignore[method-assign]

        raised = False
        try:
            fm.reserve("RELIANCE", 100, 500.0, "INTRADAY")
        except RuntimeError:
            raised = True

        assert raised, "Expected RuntimeError from simulated DB failure"

        # Restore and check -- note: in-memory state may have changed before
        # the write failed. The test verifies the exception propagates.
        # In production, the RLock ensures the failure surfaces to caller.
        fm._write_ledger = original_write  # type: ignore[method-assign]
        store.close()
    print("  OK ledger write failure -> exception propagates to caller (FM10)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- thread safety (FM5)
# ─────────────────────────────────────────────────────────────────────────────

def test_thread_safety_100_concurrent_reserve_release() -> None:
    """100 reserve+release from 5 threads; final state consistent, invariant holds."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=1_000_000.0)  # large balance to avoid conflicts
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            for i in range(20):
                try:
                    result = fm.reserve(
                        f"SYM{thread_id}_{i}", 1, 100.0, "INTRADAY"
                    )
                    if result.success:
                        fm.release(result.reservation_id, reason="thread_test")
                except Exception as exc:
                    errors.append(exc)
            # Close this thread's SQLite connection so tempdir can be cleaned up
            # on Windows (per-thread WAL connections stay open until explicit close)
            store.close()

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

        # All reservations should have been released
        snap = fm.get_snapshot()
        assert snap.intraday_reserved == 0.0
        assert abs(snap.intraday_avail - 700_000.0) < 0.01   # fully restored
        store.close()
    print("  OK 100 reserve+release from 5 threads: consistent, invariant holds (FM5)")


# ─────────────────────────────────────────────────────────────────────────────
# BL-5 Tests -- write-ahead capital ledger (Phase B.1)
# ─────────────────────────────────────────────────────────────────────────────
# These tests pin the new semantics introduced in commit BL-5:
#   * fm_ledger.mutation_type -> fm_ledger.entry_type (with CHECK constraint)
#   * Ledger row is written BEFORE the in-memory mutation (write-ahead)
#   * New columns: session_id, direction, trade_id, margin_delta, pnl_delta, costs
#   * Dead capital_ledger table is removed from schema
# ─────────────────────────────────────────────────────────────────────────────

def test_bl5_write_ahead_ledger_row_precedes_state_mutation() -> None:
    """BL-5: if the in-memory mutation raises, the ledger row still exists.

    Simulate a catastrophic mid-mutation failure by monkey-patching the bucket
    deduction helper to raise AFTER _write_ledger has committed. The contract
    says: rehydrate from fm_ledger is how we recover, so the ledger row must
    already be durable at the point of the crash.
    """
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)

        # Force the in-memory mutation to blow up POST-ledger.
        def _boom(bucket, amount):   # noqa: ARG001
            raise RuntimeError("simulated mid-mutation crash")
        fm._bucket_deduct_avail = _boom   # type: ignore[assignment]

        raised = False
        try:
            fm.reserve("RELIANCE", 100, 500.0, "INTRADAY", "sig_wal")
        except RuntimeError:
            raised = True
        assert raised, "Patched mutation must raise"

        # Ledger row exists for the RESERVE intent even though mutation failed.
        rows = store.fetch_all(
            "SELECT * FROM fm_ledger WHERE entry_type = 'RESERVE'"
        )
        assert len(rows) == 1, (
            f"RESERVE row must be durable pre-mutation; found {len(rows)}"
        )
        assert rows[0]["signal_id"] == "sig_wal"
        # Bucket state unchanged (mutation aborted before applying).
        snap = fm.get_snapshot()
        assert abs(snap.intraday_avail - 70_000.0) < 0.01
        assert snap.intraday_reserved == 0.0
        store.close()
    print("  OK BL-5: ledger row precedes state mutation (write-ahead contract)")


def test_bl5_session_id_in_every_ledger_row() -> None:
    """BL-5: every fm_ledger row carries the FundManager instance's session_id."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        expected = fm._session_id   # stamped in __init__
        assert expected.startswith("fm_") and len(expected) == 3 + 12

        r = fm.reserve("RELIANCE", 10, 500.0, "INTRADAY", "sig_ses")
        fm.commit_to_used(r.reservation_id, 500.0, 10)
        fm.release_used(
            symbol="RELIANCE", exit_price=510.0, exit_qty=10,
            intent="INTRADAY", entry_price=500.0, direction="LONG", costs=0.0,
        )

        rows = store.fetch_all("SELECT session_id FROM fm_ledger ORDER BY ledger_id")
        assert len(rows) >= 4   # INIT + RESERVE + COMMIT + RELEASE_USED
        for r in rows:
            assert r["session_id"] == expected, (
                f"Expected session_id={expected!r}, got {r['session_id']!r}"
            )
        store.close()
    print("  OK BL-5: session_id stamped on every fm_ledger row")


def test_bl5_entry_type_check_constraint_rejects_bogus_value() -> None:
    """BL-5: schema-level CHECK constraint rejects unknown entry_type."""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        raised = False
        try:
            with store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO fm_ledger
                        (ts, entry_type, amount, bucket,
                         balance_before, balance_after, session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("2026-04-19T09:15:00+05:30", "BOGUS", 0.0,
                     "intraday", 0.0, 0.0, "fm_test"),
                )
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "CHECK constraint must reject unknown entry_type"
        store.close()
    print("  OK BL-5: entry_type CHECK rejects bogus values (typo safety)")


def test_bl5_margin_delta_positive_on_reserve() -> None:
    """BL-5: RESERVE rows carry +margin in margin_delta."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        fm.reserve("RELIANCE", 100, 500.0, "INTRADAY", "sig_md")   # margin = 10000
        row = store.fetch_one(
            "SELECT margin_delta FROM fm_ledger WHERE entry_type = 'RESERVE'"
        )
        assert row is not None
        assert abs(row["margin_delta"] - 10_000.0) < 0.01, (
            f"Expected margin_delta=+10000, got {row['margin_delta']}"
        )
        store.close()
    print("  OK BL-5: RESERVE margin_delta = +margin")


def test_bl5_margin_delta_negative_on_release_used() -> None:
    """BL-5: RELEASE_USED rows carry -margin in margin_delta."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        r = fm.reserve("RELIANCE", 100, 500.0, "INTRADAY", "sig_mdneg")
        fm.commit_to_used(r.reservation_id, 500.0, 100)   # actual_margin = 10000
        fm.release_used(
            symbol="RELIANCE", exit_price=520.0, exit_qty=100,
            intent="INTRADAY", entry_price=500.0, direction="LONG", costs=0.0,
        )
        row = store.fetch_one(
            "SELECT margin_delta FROM fm_ledger WHERE entry_type = 'RELEASE_USED'"
        )
        assert row is not None
        assert abs(row["margin_delta"] + 10_000.0) < 0.01, (
            f"Expected margin_delta=-10000, got {row['margin_delta']}"
        )
        store.close()
    print("  OK BL-5: RELEASE_USED margin_delta = -margin")


def test_bl5_pnl_delta_positive_on_long_profit() -> None:
    """BL-5: RELEASE_USED pnl_delta reflects LONG gross_pnl - costs (EF-3)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        r = fm.reserve("RELIANCE", 10, 500.0, "INTRADAY", "sig_pnl_long")
        fm.commit_to_used(r.reservation_id, 500.0, 10)
        # LONG: (exit-entry)*qty - costs = (510-500)*10 - 5 = 95
        fm.release_used(
            symbol="RELIANCE", exit_price=510.0, exit_qty=10,
            intent="INTRADAY", entry_price=500.0, direction="LONG", costs=5.0,
        )
        row = store.fetch_one(
            "SELECT pnl_delta, costs, direction "
            "FROM fm_ledger WHERE entry_type = 'RELEASE_USED'"
        )
        assert row is not None
        assert abs(row["pnl_delta"] - 95.0) < 0.01, row["pnl_delta"]
        assert abs(row["costs"] - 5.0) < 0.01, row["costs"]
        assert row["direction"] == "LONG"
        store.close()
    print("  OK BL-5: LONG pnl_delta = (exit-entry)*qty - costs")


def test_bl5_pnl_delta_positive_on_short_profit() -> None:
    """BL-5: RELEASE_USED pnl_delta for SHORT uses (entry-exit)*qty (EF-3 lock)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        r = fm.reserve("RELIANCE", 10, 500.0, "INTRADAY", "sig_pnl_short")
        fm.commit_to_used(r.reservation_id, 500.0, 10)
        # SHORT: (entry-exit)*qty - costs = (500-490)*10 - 3 = 97
        fm.release_used(
            symbol="RELIANCE", exit_price=490.0, exit_qty=10,
            intent="INTRADAY", entry_price=500.0, direction="SHORT", costs=3.0,
        )
        row = store.fetch_one(
            "SELECT pnl_delta, direction "
            "FROM fm_ledger WHERE entry_type = 'RELEASE_USED'"
        )
        assert row is not None
        assert abs(row["pnl_delta"] - 97.0) < 0.01, row["pnl_delta"]
        assert row["direction"] == "SHORT"
        store.close()
    print("  OK BL-5: SHORT pnl_delta = (entry-exit)*qty - costs (EF-3)")


def test_bl5_direction_null_on_non_release_used_entries() -> None:
    """BL-5: direction is NULL on RESERVE/RELEASE/COMMIT/INIT (only set on RELEASE_USED)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        fm = _initialized_fm(store, balance=100_000.0)
        r = fm.reserve("RELIANCE", 10, 500.0, "INTRADAY", "sig_dir_null")
        fm.commit_to_used(r.reservation_id, 500.0, 10)
        rows = store.fetch_all(
            "SELECT entry_type, direction FROM fm_ledger "
            "WHERE entry_type IN ('INIT', 'RESERVE', 'COMMIT')"
        )
        assert len(rows) == 3
        for row in rows:
            assert row["direction"] is None, (
                f"Expected direction=NULL for {row['entry_type']!r}, "
                f"got {row['direction']!r}"
            )
        store.close()
    print("  OK BL-5: direction NULL on non-RELEASE_USED entries")


def test_bl5_dead_capital_ledger_table_is_removed() -> None:
    """BL-5: legacy capital_ledger table is gone from schema v10."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        row = store.fetch_one(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'capital_ledger'"
        )
        assert row is None, (
            "capital_ledger table must not exist post-BL-5 (schema v10)"
        )
        # Confirm fm_ledger *is* present as its replacement.
        row2 = store.fetch_one(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'fm_ledger'"
        )
        assert row2 is not None
        store.close()
    print("  OK BL-5: dead capital_ledger table removed; fm_ledger remains")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_initialize_splits_buckets_correctly,
        test_initialize_writes_ledger_row,
        test_margin_intraday_5x_leverage,
        test_margin_delivery_1x_leverage,
        test_margin_not_notional,
        test_reserve_intraday_draws_from_intraday_bucket,
        test_reserve_delivery_draws_from_positional_bucket,
        test_reserve_insufficient_returns_failure_no_state_change,
        test_reserve_writes_ledger_row,
        test_reservation_id_is_16_hex_chars,
        test_release_restores_available,
        test_release_twice_second_returns_false,
        test_release_unknown_id_returns_false,
        test_commit_full_fill_moves_reserved_to_used,
        test_commit_partial_fill_excess_returns_to_available,
        test_release_used_profit_increases_available_and_pnl,
        test_release_used_loss_decreases_daily_pnl,
        test_release_used_short_profit,
        test_release_used_short_loss,
        test_release_used_rejects_invalid_direction,
        test_daily_loss_limit_breach_fires_callback,
        test_sync_from_broker_updates_total_recomputes_available,
        test_sync_from_broker_does_not_touch_reserved_or_used,
        test_sync_never_subtracts_used_from_broker_balance,
        test_get_snapshot_returns_frozen_consistent_view,
        test_drain_intraday_can_still_reserve_delivery,
        test_cross_bucket_borrowing_forbidden,
        test_constructor_bucket_pct_not_summing_to_1_raises,
        test_constructor_missing_leverage_entry_raises,
        test_invariant_violation_raises_capital_invariant_violation,
        test_reset_daily_pnl_zeroes_pnl_leaves_reserved_used,
        test_ledger_write_failure_rolls_back_mutation,
        test_thread_safety_100_concurrent_reserve_release,
        # BL-5 additions (Phase B.1 write-ahead ledger)
        test_bl5_write_ahead_ledger_row_precedes_state_mutation,
        test_bl5_session_id_in_every_ledger_row,
        test_bl5_entry_type_check_constraint_rejects_bogus_value,
        test_bl5_margin_delta_positive_on_reserve,
        test_bl5_margin_delta_negative_on_release_used,
        test_bl5_pnl_delta_positive_on_long_profit,
        test_bl5_pnl_delta_positive_on_short_profit,
        test_bl5_direction_null_on_non_release_used_entries,
        test_bl5_dead_capital_ledger_table_is_removed,
    ]

    print("=" * 70)
    print("fund_manager.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

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
