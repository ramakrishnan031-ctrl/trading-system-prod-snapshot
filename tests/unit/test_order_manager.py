"""
tests/unit/test_order_manager.py

Unit tests for orders/order_manager.py. Covers the public write/read API
introduced or extended during the BL-7/BL-10 spine fix (A.3.d).

Current scope: close_trade() (BL-10a).

Run: python -m pytest tests/unit/test_order_manager.py -v
Or:  python tests/unit/test_order_manager.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from orders.order_manager import OrderManager


_SCHEMA_PATH = Path(__file__).parent.parent.parent / "core" / "schema.sql"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "test.db", _SCHEMA_PATH)


def _make_om(store: StateStore) -> OrderManager:
    return OrderManager(state_store=store, logger=logging.getLogger("test_om"))


def _seed_signal(store: StateStore, signal_id: str = "sig_1") -> None:
    """Insert a parent signal row to satisfy trades.signal_id FK."""
    now = "2026-04-16T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (signal_id, "RELIANCE", "SCANNER", "vwap_bounce_long", now, now,
             "2026-04-16T09:35:00+05:30", "TRADED", f"fp_{signal_id}",
             "2026-04-16"),
        )


def _seed_trade(store: StateStore, om: OrderManager) -> str:
    """Create a PENDING_FILL trade (with parent signal) and return trade_id."""
    _seed_signal(store, "sig_1")
    return om.create_trade(
        signal_id="sig_1",
        symbol="RELIANCE",
        direction="LONG",
        strategy="vwap_bounce_long",
        sector=None,
        qty=10,
        entry_target_price=2500.0,
        sl_initial=2475.0,
        tgt_initial=2550.0,
        order_protocol="LIMIT_TRIPLE",
        margin_reserved=5000.0,
        risk_amount=250.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# close_trade() — BL-10a
# ─────────────────────────────────────────────────────────────────────────────

def test_close_trade_happy_path(tmp_path: Path) -> None:
    """close_trade sets status=CLOSED and populates all exit columns."""
    store = _make_store(tmp_path)
    om = _make_om(store)
    trade_id = _seed_trade(store, om)

    row = om.close_trade(
        trade_id=trade_id,
        exit_price=2550.0,
        exit_qty=10,
        exit_reason="TGT_HIT",
        gross_pnl=500.0,
        charges=25.0,
    )

    assert row["status"] == "CLOSED"
    assert row["exit_price"] == 2550.0
    assert row["exit_reason"] == "TGT_HIT"
    assert row["gross_pnl"] == 500.0
    assert row["charges"] == 25.0
    assert row["exit_time"] is not None
    store.close()
    print("  OK close_trade happy path sets status=CLOSED + exit cols")


def test_close_trade_computes_net_pnl(tmp_path: Path) -> None:
    """net_pnl is gross_pnl minus charges, persisted on the row."""
    store = _make_store(tmp_path)
    om = _make_om(store)
    trade_id = _seed_trade(store, om)

    row = om.close_trade(
        trade_id=trade_id,
        exit_price=2550.0,
        exit_qty=10,
        exit_reason="TGT_HIT",
        gross_pnl=500.0,
        charges=37.50,
    )
    assert abs(row["net_pnl"] - (500.0 - 37.50)) < 1e-6
    store.close()
    print("  OK close_trade computes net_pnl = gross - charges")


def test_close_trade_rejects_invalid_exit_reason(tmp_path: Path) -> None:
    """Any exit_reason outside the frozenset raises ValueError."""
    store = _make_store(tmp_path)
    om = _make_om(store)
    trade_id = _seed_trade(store, om)

    with pytest.raises(ValueError, match="exit_reason must be one of"):
        om.close_trade(
            trade_id=trade_id,
            exit_price=2550.0,
            exit_qty=10,
            exit_reason="BOGUS_REASON",
            gross_pnl=0.0,
            charges=0.0,
        )
    store.close()
    print("  OK close_trade rejects invalid exit_reason")


def test_close_trade_rejects_unknown_trade_id(tmp_path: Path) -> None:
    """Closing a trade that doesn't exist raises ValueError."""
    store = _make_store(tmp_path)
    om = _make_om(store)

    with pytest.raises(ValueError, match="not found"):
        om.close_trade(
            trade_id="trd_does_not_exist",
            exit_price=100.0,
            exit_qty=1,
            exit_reason="MANUAL_CLOSE",
            gross_pnl=0.0,
            charges=0.0,
        )
    store.close()
    print("  OK close_trade rejects unknown trade_id")


def test_close_trade_rejects_double_close(tmp_path: Path) -> None:
    """Closing an already-CLOSED trade raises ValueError (no overwrite)."""
    store = _make_store(tmp_path)
    om = _make_om(store)
    trade_id = _seed_trade(store, om)

    om.close_trade(
        trade_id=trade_id,
        exit_price=2550.0,
        exit_qty=10,
        exit_reason="TGT_HIT",
        gross_pnl=500.0,
        charges=25.0,
    )
    with pytest.raises(ValueError, match="already CLOSED"):
        om.close_trade(
            trade_id=trade_id,
            exit_price=2560.0,
            exit_qty=10,
            exit_reason="MANUAL_CLOSE",
            gross_pnl=600.0,
            charges=30.0,
        )
    store.close()
    print("  OK close_trade rejects double-close")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    import tempfile

    tests = [
        test_close_trade_happy_path,
        test_close_trade_computes_net_pnl,
        test_close_trade_rejects_invalid_exit_reason,
        test_close_trade_rejects_unknown_trade_id,
        test_close_trade_rejects_double_close,
    ]

    print("=" * 70)
    print("order_manager.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                test(Path(tmp))
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
