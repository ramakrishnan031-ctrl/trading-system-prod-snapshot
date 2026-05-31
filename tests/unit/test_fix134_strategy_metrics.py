"""Tests for FIX-134 Item 36: strategy Sharpe ratio calculation."""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from scripts.compute_strategy_metrics import (
    compute_sharpe,
    compute_win_rate,
    run_strategy_metrics,
    _check_demotion,
    _SHARPE_DEMOTION_THRESHOLD,
    _ANNUALIZATION_FACTOR,
)


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


def _insert_closed_trade(store, strategy, net_pnl, date_str):
    """Insert a CLOSED trade for the given strategy on the given date."""
    ts = f"{date_str}T14:00:00+05:30"
    trade_id = str(uuid.uuid4())
    signal_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, 'TEST', 'test', ?, ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (signal_id, strategy, ts, ts, ts, f"fp-{signal_id}", date_str),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                qty_filled, entry_target_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, created_at, status,
                order_protocol, net_pnl, exit_time, updated_at)
               VALUES (?, ?, 'TEST', 'LONG', ?, 10, 10, 100.0, 95.0, 110.0,
                       2000.0, 500.0, ?, 'CLOSED', 'LIMIT_TRIPLE', ?, ?, ?)""",
            (trade_id, signal_id, strategy, ts, net_pnl, ts, ts),
        )


def _dates_back(n: int) -> list[str]:
    """Return list of date strings going back n days from today."""
    base = datetime.strptime(today_ist(), "%Y-%m-%d")
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


# ── Sharpe calculation ─────────────────────────────────────────────────────


class TestComputeSharpe:
    def test_insufficient_data_returns_none(self, store):
        result = compute_sharpe(store, "strat_a", today_ist())
        assert result is None

    def test_one_day_returns_none(self, store):
        _insert_closed_trade(store, "strat_a", 100.0, today_ist())
        result = compute_sharpe(store, "strat_a", today_ist())
        assert result is None

    def test_positive_sharpe(self, store):
        dates = _dates_back(5)
        for d in dates:
            _insert_closed_trade(store, "strat_a", 100.0, d)
        result = compute_sharpe(store, "strat_a", today_ist())
        assert result is not None
        # All same P&L -> std = 0 -> sharpe = 0
        assert result == 0.0

    def test_mixed_returns_nonzero_sharpe(self, store):
        dates = _dates_back(5)
        pnls = [100.0, -50.0, 200.0, -30.0, 150.0]
        for d, pnl in zip(dates, pnls):
            _insert_closed_trade(store, "strat_b", pnl, d)
        result = compute_sharpe(store, "strat_b", today_ist())
        assert result is not None
        assert isinstance(result, float)

    def test_negative_sharpe(self, store):
        dates = _dates_back(5)
        pnls = [-100.0, -50.0, 10.0, -200.0, -30.0]
        for d, pnl in zip(dates, pnls):
            _insert_closed_trade(store, "strat_c", pnl, d)
        result = compute_sharpe(store, "strat_c", today_ist())
        assert result is not None
        assert result < 0

    def test_sharpe_annualized(self, store):
        dates = _dates_back(4)
        pnls = [100.0, -50.0, 200.0, -10.0]
        for d, pnl in zip(dates, pnls):
            _insert_closed_trade(store, "strat_d", pnl, d)
        result = compute_sharpe(store, "strat_d", today_ist())
        mean_pnl = sum(pnls) / len(pnls)
        var = sum((x - mean_pnl) ** 2 for x in pnls) / (len(pnls) - 1)
        expected = (mean_pnl / math.sqrt(var)) * _ANNUALIZATION_FACTOR
        assert result is not None
        assert abs(result - expected) < 0.01


# ── Win rate ───────────────────────────────────────────────────────────────


class TestComputeWinRate:
    def test_no_trades(self, store):
        wr, avg, total = compute_win_rate(store, "none", today_ist())
        assert wr is None
        assert avg is None
        assert total == 0

    def test_all_winners(self, store):
        dates = _dates_back(3)
        for d in dates:
            _insert_closed_trade(store, "winners", 100.0, d)
        wr, avg, total = compute_win_rate(store, "winners", today_ist())
        assert wr == 1.0
        assert avg == 100.0
        assert total == 3

    def test_mixed_win_rate(self, store):
        dates = _dates_back(4)
        pnls = [100.0, -50.0, 200.0, -30.0]
        for d, pnl in zip(dates, pnls):
            _insert_closed_trade(store, "mixed", pnl, d)
        wr, avg, total = compute_win_rate(store, "mixed", today_ist())
        assert wr == 0.5
        assert total == 4


# ── End-to-end run ─────────────────────────────────────────────────────────


class TestRunStrategyMetrics:
    def test_computes_and_stores(self, store):
        dates = _dates_back(5)
        for d in dates:
            _insert_closed_trade(store, "gap_go_long", 100.0, d)
        results = run_strategy_metrics(
            store=store,
            date_iso=today_ist(),
            log=logging.getLogger("test"),
        )
        assert len(results) == 1
        assert results[0]["strategy"] == "gap_go_long"
        row = store.fetch_one(
            "SELECT * FROM strategy_metrics WHERE strategy = 'gap_go_long'"
        )
        assert row is not None

    def test_dry_run_no_db_write(self, store):
        dates = _dates_back(3)
        for d in dates:
            _insert_closed_trade(store, "dry_test", 50.0, d)
        run_strategy_metrics(
            store=store,
            date_iso=today_ist(),
            log=logging.getLogger("test"),
            dry_run=True,
        )
        row = store.fetch_one(
            "SELECT * FROM strategy_metrics WHERE strategy = 'dry_test'"
        )
        assert row is None

    def test_multiple_strategies(self, store):
        dates = _dates_back(3)
        for d in dates:
            _insert_closed_trade(store, "strat_x", 100.0, d)
            _insert_closed_trade(store, "strat_y", -50.0, d)
        results = run_strategy_metrics(
            store=store,
            date_iso=today_ist(),
            log=logging.getLogger("test"),
        )
        names = {r["strategy"] for r in results}
        assert "strat_x" in names
        assert "strat_y" in names


# ── Demotion logic ─────────────────────────────────────────────────────────


class TestDemotion:
    def test_no_demotion_with_good_sharpe(self, store):
        dates = _dates_back(3)
        now_str = datetime.now().isoformat()
        for d in dates:
            with store.transaction() as cur:
                cur.execute(
                    """INSERT OR REPLACE INTO strategy_metrics
                       (strategy, date, sharpe, win_rate, avg_pnl, total_trades, computed_at)
                       VALUES (?, ?, ?, 0.5, 100.0, 10, ?)""",
                    ("good_strat", d, 0.5, now_str),
                )
        result = _check_demotion(
            store, "good_strat", logging.getLogger("test")
        )
        assert result is False

    def test_demotion_triggers_on_bad_sharpe(self, store):
        dates = _dates_back(3)
        now_str = datetime.now().isoformat()
        for d in dates:
            with store.transaction() as cur:
                cur.execute(
                    """INSERT OR REPLACE INTO strategy_metrics
                       (strategy, date, sharpe, win_rate, avg_pnl, total_trades, computed_at)
                       VALUES (?, ?, ?, 0.3, -50.0, 10, ?)""",
                    ("bad_strat", d, -0.5, now_str),
                )
        result = _check_demotion(
            store, "bad_strat", logging.getLogger("test")
        )
        assert result is True

    def test_demotion_sends_telegram(self, store):
        dates = _dates_back(3)
        now_str = datetime.now().isoformat()
        for d in dates:
            with store.transaction() as cur:
                cur.execute(
                    """INSERT OR REPLACE INTO strategy_metrics
                       (strategy, date, sharpe, win_rate, avg_pnl, total_trades, computed_at)
                       VALUES (?, ?, ?, 0.3, -50.0, 10, ?)""",
                    ("demote_strat", d, -0.5, now_str),
                )
        notifier = MagicMock()
        _check_demotion(
            store, "demote_strat", logging.getLogger("test"), notifier=notifier
        )
        assert notifier.send.called
        call_kwargs = notifier.send.call_args
        assert "DEMOTED" in call_kwargs.kwargs.get("title", call_kwargs[1].get("title", ""))

    def test_no_demotion_insufficient_data(self, store):
        dates = _dates_back(2)
        now_str = datetime.now().isoformat()
        for d in dates:
            with store.transaction() as cur:
                cur.execute(
                    """INSERT OR REPLACE INTO strategy_metrics
                       (strategy, date, sharpe, win_rate, avg_pnl, total_trades, computed_at)
                       VALUES (?, ?, ?, 0.3, -50.0, 10, ?)""",
                    ("short_strat", d, -0.5, now_str),
                )
        result = _check_demotion(
            store, "short_strat", logging.getLogger("test")
        )
        assert result is False

    def test_parity_same_computation(self, store):
        """Paper and live use the same Sharpe computation."""
        dates = _dates_back(3)
        for d in dates:
            _insert_closed_trade(store, "parity_test", 100.0, d)
        sharpe = compute_sharpe(store, "parity_test", today_ist())
        assert sharpe is not None
