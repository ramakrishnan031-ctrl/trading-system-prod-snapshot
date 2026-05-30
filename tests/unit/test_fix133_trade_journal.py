"""
tests/unit/test_fix133_trade_journal.py

FIX-133 Item 30: Automated daily trade journal.
  - Journal entries created with correct fields
  - Idempotent: re-run replaces existing entries
  - No trades -> 0 entries
  - Slippage assessment: HIGH if > 0.5%
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from scripts.trade_journal import generate_journal


def _log():
    return logging.getLogger("test_trade_journal")


def _make_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "test.db")
    return store


def _seed_trade(store: StateStore, date_iso="2026-05-31"):
    conn = store._get_conn()
    # Insert signal first (FK)
    conn.execute(
        """INSERT INTO signals (signal_id, symbol, scanner, strategy,
           triggered_at, received_at, expires_at, status, trigger_price,
           fingerprint, fingerprint_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("sig1", "RELIANCE", "gap_scanner", "gap_go_long",
         f"{date_iso}T09:30:00", f"{date_iso}T09:30:00",
         f"{date_iso}T10:00:00", "TRADED", 2500.0,
         "fp1", date_iso),
    )
    # Insert trade
    conn.execute(
        """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
           qty_planned, qty_filled, entry_target_price, entry_actual_price,
           sl_initial, tgt_initial, margin_reserved, risk_amount,
           created_at, exit_price, exit_reason, net_pnl, status,
           order_protocol, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("t1", "sig1", "RELIANCE", "LONG", "gap_go_long",
         10, 10, 2500.0, 2505.0, 2450.0, 2600.0,
         5000.0, 500.0, f"{date_iso}T09:35:00",
         2580.0, "TGT_HIT", 750.0, "CLOSED",
         "CO_PLUS_TGT", f"{date_iso}T14:00:00"),
    )
    # Link signal to trade
    conn.execute("UPDATE signals SET trade_id = 't1' WHERE signal_id = 'sig1'")
    # Screener result
    conn.execute(
        """INSERT INTO screener_results
           (signal_id, score, tier, status, step_results, latencies, market_data_snapshot, ts)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("sig1", 82, "HIGH", "PASSED", "{}", "{}", "{}", f"{date_iso}T09:31:00"),
    )
    conn.commit()


class TestTradeJournal:

    def test_journal_entry_created(self, tmp_path) -> None:
        """Journal entry has correct fields from trade data."""
        store = _make_store(tmp_path)
        _seed_trade(store)

        count = generate_journal(store, "2026-05-31", _log())
        assert count == 1

        row = store.fetch_one("SELECT * FROM trade_journal WHERE trade_id = 't1'")
        assert row is not None
        d = dict(row)
        assert d["strategy"] == "gap_go_long"
        assert d["symbol"] == "RELIANCE"
        assert d["direction"] == "LONG"
        assert d["exit_reason"] == "TGT_HIT"
        assert "gap_scanner" in d["entry_reason"]
        assert "score=82" in d["entry_reason"]
        assert d["entry_price"] == 2505.0
        assert d["exit_price"] == 2580.0
        assert d["net_pnl"] == 750.0
        print("  OK: journal entry created with correct fields")

    def test_slippage_assessment(self, tmp_path) -> None:
        """Slippage > 0.5% -> HIGH."""
        store = _make_store(tmp_path)
        _seed_trade(store)
        # trigger_price=2500, entry_actual=2505 -> 0.2% -> LOW
        generate_journal(store, "2026-05-31", _log())
        row = store.fetch_one("SELECT slippage_assessment FROM trade_journal WHERE trade_id = 't1'")
        assert row is not None
        assert dict(row)["slippage_assessment"] == "LOW"
        print("  OK: 0.2% slippage -> LOW assessment")

    def test_idempotent_rerun(self, tmp_path) -> None:
        """Re-running for same date replaces existing entries."""
        store = _make_store(tmp_path)
        _seed_trade(store)

        generate_journal(store, "2026-05-31", _log())
        generate_journal(store, "2026-05-31", _log())  # re-run

        rows = store.fetch_all("SELECT * FROM trade_journal WHERE date = '2026-05-31'")
        assert len(rows) == 1, f"Expected 1 entry (idempotent), got {len(rows)}"
        print("  OK: idempotent re-run -> still 1 entry")

    def test_no_trades_zero_entries(self, tmp_path) -> None:
        """No closed trades -> 0 entries."""
        store = _make_store(tmp_path)
        count = generate_journal(store, "2026-05-31", _log())
        assert count == 0
        print("  OK: no trades -> 0 entries")

    def test_date_column_populated(self, tmp_path) -> None:
        """Date column matches the requested date."""
        store = _make_store(tmp_path)
        _seed_trade(store, date_iso="2026-05-30")
        generate_journal(store, "2026-05-30", _log())

        row = store.fetch_one("SELECT date FROM trade_journal WHERE trade_id = 't1'")
        assert row is not None
        assert dict(row)["date"] == "2026-05-30"
        print("  OK: date column = 2026-05-30")


if __name__ == "__main__":
    import tempfile
    tests = [
        ("journal_entry", TestTradeJournal().test_journal_entry_created),
        ("slippage", TestTradeJournal().test_slippage_assessment),
        ("idempotent", TestTradeJournal().test_idempotent_rerun),
        ("no_trades", TestTradeJournal().test_no_trades_zero_entries),
        ("date_col", TestTradeJournal().test_date_column_populated),
    ]
    passed = 0
    for name, t in tests:
        try:
            with tempfile.TemporaryDirectory() as d:
                t(Path(d))
            passed += 1
        except Exception as exc:
            print(f"  FAIL {name}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
