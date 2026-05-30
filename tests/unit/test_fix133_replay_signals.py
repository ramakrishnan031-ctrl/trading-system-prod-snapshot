"""
tests/unit/test_fix133_replay_signals.py

FIX-133 Item 19: Replay mode for historical signal re-running.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from scripts.replay_signals import (
    fetch_signals,
    replay_one_signal,
    run_replay,
    write_csv,
)


def _log():
    return logging.getLogger("test_replay")


def _make_store(tmp_dir: Path) -> StateStore:
    """Create a StateStore with full schema from schema.sql."""
    db_path = tmp_dir / "test.db"
    store = StateStore(db_path)
    return store


def _seed_traded_signal(conn, signal_id, trade_id, symbol, strategy, direction="LONG",
                        trigger_price=2500.0, pnl=500.0, date="2026-05-10"):
    """Insert signal + trade in FK-safe order: signal first, then trade, then link."""
    conn.execute(
        """INSERT INTO signals (signal_id, symbol, scanner, strategy,
           triggered_at, received_at, expires_at, status, rejection_reason,
           trade_id, trigger_price, fingerprint, fingerprint_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (signal_id, symbol, "scanner1", strategy,
         f"{date}T09:30:00", f"{date}T09:30:00", f"{date}T10:00:00",
         "TRADED", None, None, trigger_price,
         f"fp_{signal_id}", date),
    )
    conn.execute(
        """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
           qty_planned, qty_filled, entry_target_price, entry_actual_price,
           sl_initial, tgt_initial, margin_reserved, risk_amount,
           created_at, net_pnl, status, order_protocol, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (trade_id, signal_id, symbol, direction, strategy,
         10, 10, trigger_price, trigger_price,
         trigger_price * 0.98, trigger_price * 1.04,
         trigger_price * 10 * 0.2, trigger_price * 0.02 * 10,
         f"{date}T10:00:00", pnl, "CLOSED", "CO_PLUS_TGT",
         f"{date}T10:00:00"),
    )
    conn.execute(
        "UPDATE signals SET trade_id = ? WHERE signal_id = ?",
        (trade_id, signal_id),
    )


def _insert_signal_only(conn, signal_id, symbol, strategy, status,
                        trigger_price=2500.0, date="2026-05-10", rejection=None):
    """Insert a signal with no trade (no FK issue)."""
    conn.execute(
        """INSERT INTO signals (signal_id, symbol, scanner, strategy,
           triggered_at, received_at, expires_at, status, rejection_reason,
           trade_id, trigger_price, fingerprint, fingerprint_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (signal_id, symbol, "scanner1", strategy,
         f"{date}T09:30:00", f"{date}T09:30:00", f"{date}T10:00:00",
         status, rejection, None, trigger_price,
         f"fp_{signal_id}", date),
    )


class TestFetchSignals:

    def test_fetch_signals_in_date_range(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        conn = store._get_conn()
        _seed_traded_signal(conn, "sig1", "t1", "RELIANCE", "gap_go_long")
        _insert_signal_only(conn, "sig2", "TCS", "gap_go_long", "REJECTED_SCREENER",
                            trigger_price=3200.0, date="2026-05-11", rejection="Low score")
        conn.commit()

        results = fetch_signals(store, "2026-05-10", "2026-05-11")
        assert len(results) == 2
        assert results[0]["signal_id"] == "sig1"
        assert results[1]["signal_id"] == "sig2"
        print("  OK: fetch_signals returns signals in date range")


class TestReplayOneSignal:

    def test_traded_signal_replays_as_would_trade(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        conn = store._get_conn()
        _seed_traded_signal(conn, "sig1", "t1", "RELIANCE", "gap_go_long")
        conn.execute(
            """INSERT INTO screener_results
               (signal_id, score, tier, status, step_results, latencies, market_data_snapshot, ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("sig1", 75, "HIGH", "PASSED", "{}", "{}", "{}", "2026-05-10T09:31:00"),
        )
        conn.commit()

        signal = dict(store.fetch_one("SELECT * FROM signals WHERE signal_id = 'sig1'"))

        from strategies.loader import StrategyLoader
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        result = replay_one_signal(signal, strategies, store, _log())
        assert result["replay_decision"] == "WOULD_TRADE"
        assert result["replay_qty"] > 0
        assert result["original_pnl"] == "500.0"
        print(f"  OK: traded signal replays as WOULD_TRADE (qty={result['replay_qty']})")

    def test_unknown_strategy_skipped(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        signal = {
            "signal_id": "sig_x",
            "symbol": "FAKE",
            "strategy": "nonexistent_strategy",
            "scanner": "test",
            "trigger_price": 100.0,
            "status": "TRADED",
            "rejection_reason": None,
            "trade_id": None,
        }
        result = replay_one_signal(signal, {}, store, _log())
        assert result["replay_decision"] == "SKIPPED"
        assert "not in current config" in result["replay_reason"]
        print("  OK: unknown strategy -> SKIPPED")

    def test_no_trigger_price_skipped(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        signal = {
            "signal_id": "sig_y",
            "symbol": "TEST",
            "strategy": "gap_go_long",
            "scanner": "test",
            "trigger_price": None,
            "status": "TRADED",
            "rejection_reason": None,
            "trade_id": None,
        }
        from strategies.loader import StrategyLoader
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        result = replay_one_signal(signal, strategies, store, _log())
        assert result["replay_decision"] == "SKIPPED"
        assert "trigger_price" in result["replay_reason"]
        print("  OK: no trigger_price -> SKIPPED")


class TestWriteCsv:

    def test_csv_written_with_correct_columns(self, tmp_path) -> None:
        results = [{
            "signal_id": "s1", "symbol": "RELIANCE", "strategy": "gap_go_long",
            "scanner": "sc1", "trigger_price": 2500.0,
            "original_status": "TRADED", "original_rejection": "",
            "original_trade_id": "t1", "original_pnl": "500.0",
            "replay_decision": "WOULD_TRADE", "replay_qty": 10,
            "replay_reason": "qty=10",
        }]
        out = tmp_path / "replay.csv"
        write_csv(results, out)

        assert out.exists()
        import csv
        with open(out, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["signal_id"] == "s1"
        assert rows[0]["replay_decision"] == "WOULD_TRADE"
        print("  OK: CSV written with correct columns")


if __name__ == "__main__":
    import tempfile
    tests = [
        ("fetch_signals", TestFetchSignals().test_fetch_signals_in_date_range),
        ("traded_replay", TestReplayOneSignal().test_traded_signal_replays_as_would_trade),
        ("unknown_strat", TestReplayOneSignal().test_unknown_strategy_skipped),
        ("no_trigger", TestReplayOneSignal().test_no_trigger_price_skipped),
        ("csv_write", TestWriteCsv().test_csv_written_with_correct_columns),
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
