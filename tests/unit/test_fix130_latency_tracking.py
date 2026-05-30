"""
tests/unit/test_fix130_latency_tracking.py

Tests for FIX-130 Item 5: signal-to-fill latency tracking.
  - Schema v17 has signal_to_order_ms, order_to_fill_ms, total_latency_ms
  - record_entry_fill() computes and stores latency metrics
  - Latency is non-negative (NTP skew clamped to 0)
  - Best-effort: missing signal/order rows don't raise
"""
from __future__ import annotations

import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.ids import new_signal_id, new_trade_id, new_order_id
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager


_IST = timezone(timedelta(hours=5, minutes=30))


def _log():
    return logging.getLogger("test_fix130_latency")


def _make_store(tmp: Path) -> StateStore:
    return StateStore(db_path=tmp / "test.db")


def _dt(h: int, m: int, s: int = 0) -> str:
    """Build an IST datetime string at given HH:MM:SS today."""
    now = datetime.now(_IST)
    dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
    return dt.isoformat()


def _seed_scenario(store: StateStore, om: OrderManager,
                   received_at: str, placed_at: str) -> tuple[str, str, str]:
    """
    Create signal + trade + ENTRY order with controlled timestamps.
    Returns (sig_id, trade_id, order_id).
    """
    sig_id = new_signal_id()
    trade_id = new_trade_id()
    order_id = new_order_id()
    now_str = now_ist().isoformat()

    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy,
               triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, 'RELIANCE', 'gap_go_long', 'gap_go_long', ?, ?, ?, 'PROCESSING', ?, ?)""",
            (sig_id, received_at, received_at, received_at, sig_id[:8], received_at[:10]),
        )
        cur.execute(
            """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, status,
               qty_planned, entry_target_price, sl_initial, tgt_initial,
               order_protocol, margin_reserved, risk_amount, created_at, updated_at)
               VALUES (?, ?, 'RELIANCE', 'LONG', 'gap_go_long', 'PENDING_FILL',
               10, 1000.0, 950.0, 1100.0, 'LIMIT_TRIPLE', 2000.0, 500.0, ?, ?)""",
            (trade_id, sig_id, now_str, now_str),
        )
        cur.execute(
            """INSERT INTO orders (order_id, trade_id, leg, leg_index,
               transaction_type, order_type, product, variety,
               qty_requested, status, placed_at, updated_at)
               VALUES (?, ?, 'ENTRY', 0, 'BUY', 'LIMIT', 'MIS', 'regular',
               10, 'COMPLETE', ?, ?)""",
            (order_id, trade_id, placed_at, now_str),
        )
        cur.execute("UPDATE signals SET trade_id = ? WHERE signal_id = ?", (trade_id, sig_id))

    return sig_id, trade_id, order_id


class TestLatencyTracking:

    def test_schema_has_latency_columns(self) -> None:
        """Schema v17 trades table has all 3 latency columns."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            sig_id = new_signal_id()
            trade_id = new_trade_id()
            now = now_ist().isoformat()
            with store.transaction() as cur:
                cur.execute(
                    """INSERT INTO signals (signal_id, symbol, scanner, strategy,
                       triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
                       VALUES (?, 'X', 'test', 'test', ?, ?, ?, 'PROCESSING', ?, ?)""",
                    (sig_id, now, now, now, sig_id[:8], now[:10]),
                )
                cur.execute(
                    """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
                       status, qty_planned, entry_target_price, sl_initial, tgt_initial,
                       order_protocol, margin_reserved, risk_amount, created_at, updated_at)
                       VALUES (?, ?, 'X', 'LONG', 'test', 'PENDING_FILL',
                       1, 100.0, 95.0, 110.0, 'LIMIT_TRIPLE', 20.0, 5.0, ?, ?)""",
                    (trade_id, sig_id, now, now),
                )
            row = store.fetch_one("SELECT signal_to_order_ms, order_to_fill_ms, total_latency_ms FROM trades WHERE trade_id = ?", (trade_id,))
            assert row is not None
            assert "signal_to_order_ms" in dict(row)
            assert "order_to_fill_ms" in dict(row)
            assert "total_latency_ms" in dict(row)
            store.close()
            print("  OK: schema v17 has latency columns (all NULL by default)")

    def test_latency_computed_on_fill(self) -> None:
        """record_entry_fill() computes and stores correct latency metrics."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            om = OrderManager(store, _log())

            received_at = _dt(9, 15, 0)   # signal received at 09:15:00
            placed_at   = _dt(9, 15, 2)   # order placed at 09:15:02 (2000ms later)
            filled_at   = _dt(9, 15, 5)   # fill at 09:15:05 (3000ms after placed, 5000ms total)

            _, trade_id, _ = _seed_scenario(store, om, received_at, placed_at)

            om.record_entry_fill(
                trade_id=trade_id,
                avg_fill_price=1000.0,
                qty_filled=10,
                filled_at=filled_at,
            )

            row = store.fetch_one(
                "SELECT signal_to_order_ms, order_to_fill_ms, total_latency_ms FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            assert row["signal_to_order_ms"] is not None, "signal_to_order_ms should be computed"
            assert row["order_to_fill_ms"] is not None, "order_to_fill_ms should be computed"
            assert row["total_latency_ms"] is not None, "total_latency_ms should be computed"
            assert abs(row["signal_to_order_ms"] - 2000) < 100, f"Expected ~2000ms, got {row['signal_to_order_ms']}"
            assert abs(row["order_to_fill_ms"] - 3000) < 100, f"Expected ~3000ms, got {row['order_to_fill_ms']}"
            assert abs(row["total_latency_ms"] - 5000) < 100, f"Expected ~5000ms, got {row['total_latency_ms']}"
            store.close()
            print(f"  OK: latency computed: sig->order={row['signal_to_order_ms']}ms, order->fill={row['order_to_fill_ms']}ms, total={row['total_latency_ms']}ms")

    def test_latency_non_negative(self) -> None:
        """Latency values are always >= 0 (clamped for NTP skew)."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            om = OrderManager(store, _log())

            # Same timestamp for all (0ms latency scenario)
            ts = _dt(9, 15, 0)
            _, trade_id, _ = _seed_scenario(store, om, ts, ts)

            om.record_entry_fill(
                trade_id=trade_id, avg_fill_price=1000.0, qty_filled=10, filled_at=ts,
            )

            row = store.fetch_one(
                "SELECT signal_to_order_ms, order_to_fill_ms, total_latency_ms FROM trades WHERE trade_id = ?",
                (trade_id,),
            )
            for col in ("signal_to_order_ms", "order_to_fill_ms", "total_latency_ms"):
                v = row[col]
                if v is not None:
                    assert v >= 0, f"{col} must be >= 0, got {v}"
            store.close()
            print("  OK: latency >= 0 (same-timestamp scenario)")

    def test_missing_signal_does_not_raise(self) -> None:
        """record_entry_fill() doesn't raise even when signal row is missing."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            om = OrderManager(store, _log())

            # Create trade without signal linked
            trade_id = new_trade_id()
            sig_id = new_signal_id()
            now = now_ist().isoformat()
            with store.transaction() as cur:
                cur.execute(
                    """INSERT INTO signals (signal_id, symbol, scanner, strategy,
                       triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
                       VALUES (?, 'X', 't', 't', ?, ?, ?, 'PROCESSING', ?, ?)""",
                    (sig_id, now, now, now, sig_id[:8], now[:10]),
                )
                cur.execute(
                    """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
                       status, qty_planned, entry_target_price, sl_initial, tgt_initial,
                       order_protocol, margin_reserved, risk_amount, created_at, updated_at)
                       VALUES (?, ?, 'X', 'LONG', 't', 'PENDING_FILL',
                       1, 100.0, 95.0, 110.0, 'LIMIT_TRIPLE', 20.0, 5.0, ?, ?)""",
                    (trade_id, sig_id, now, now),
                )

            om.record_entry_fill(
                trade_id=trade_id, avg_fill_price=100.0, qty_filled=1, filled_at=now,
            )

            row = store.fetch_one("SELECT status FROM trades WHERE trade_id = ?", (trade_id,))
            assert row["status"] == "OPEN"  # fill still recorded
            store.close()
            print("  OK: missing ENTRY order -> latency skipped, fill still recorded")


if __name__ == "__main__":
    tests = [
        TestLatencyTracking().test_schema_has_latency_columns,
        TestLatencyTracking().test_latency_computed_on_fill,
        TestLatencyTracking().test_latency_non_negative,
        TestLatencyTracking().test_missing_signal_does_not_raise,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
