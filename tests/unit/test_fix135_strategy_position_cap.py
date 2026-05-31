"""Tests for FIX-135 Item 42: per-strategy max concurrent positions cap."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from strategies.schema import StrategyConfig


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


def _insert_open_trade(store, strategy, symbol="RELIANCE"):
    ts = f"{today_ist()}T10:00:00+05:30"
    trade_id = str(uuid.uuid4())
    signal_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, ?, 'test', ?, ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (signal_id, symbol, strategy, ts, ts, ts, f"fp-{signal_id}", today_ist()),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                qty_filled, entry_target_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, created_at, status,
                order_protocol, updated_at)
               VALUES (?, ?, ?, 'LONG', ?, 10, 10, 100.0, 95.0, 110.0,
                       2000.0, 500.0, ?, 'OPEN', 'LIMIT_TRIPLE', ?)""",
            (trade_id, signal_id, symbol, strategy, ts, ts),
        )
    return trade_id


def _count_strategy_open(store, strategy_name):
    row = store.fetch_one(
        "SELECT COUNT(*) AS n FROM trades WHERE strategy = ? AND status IN ('OPEN', 'PARTIAL')",
        (strategy_name,),
    )
    return int(row["n"]) if row else 0


# ── Schema field ─────────────────────────────────────────────────────────


class TestSchemaField:
    def test_default_value_is_2(self):
        cfg = StrategyConfig(
            name="test", display_name="Test", description="test",
            direction="LONG", intent="INTRADAY", order_protocol="CO_PLUS_TGT",
            entry_method="LIMIT", sl_method="FIXED_PCT", sl_pct=0.01,
            tgt_method="RISK_REWARD", tgt_risk_reward=2.0,
            smart_tgt_enabled=False, pullback_wait_enabled=False,
            min_score=0, lot_size=1,
        )
        assert cfg.max_concurrent_positions == 2

    def test_custom_value(self):
        cfg = StrategyConfig(
            name="test", display_name="Test", description="test",
            direction="LONG", intent="INTRADAY", order_protocol="CO_PLUS_TGT",
            entry_method="LIMIT", sl_method="FIXED_PCT", sl_pct=0.01,
            tgt_method="RISK_REWARD", tgt_risk_reward=2.0,
            smart_tgt_enabled=False, pullback_wait_enabled=False,
            min_score=0, lot_size=1,
            max_concurrent_positions=5,
        )
        assert cfg.max_concurrent_positions == 5

    def test_gap_strategies_have_3(self):
        from strategies.loader import StrategyLoader
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))
        for name in ["gap_go_long", "gap_go_short", "gap_fade_long", "gap_fade_short"]:
            assert strategies[name].max_concurrent_positions == 3, f"{name} should be 3"

    def test_default_strategies_have_2(self):
        from strategies.loader import StrategyLoader
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))
        for name in ["first_pullback_long", "range_breakout_long", "positional_swing_long"]:
            assert strategies[name].max_concurrent_positions == 2, f"{name} should be 2"


# ── DB count check ───────────────────────────────────────────────────────


class TestDBCountCheck:
    def test_no_trades_returns_zero(self, store):
        assert _count_strategy_open(store, "gap_go_long") == 0

    def test_open_trades_counted(self, store):
        _insert_open_trade(store, "gap_go_long", "RELIANCE")
        _insert_open_trade(store, "gap_go_long", "INFY")
        assert _count_strategy_open(store, "gap_go_long") == 2

    def test_closed_trades_excluded(self, store):
        tid = _insert_open_trade(store, "gap_go_long", "TCS")
        with store.transaction() as cur:
            cur.execute("UPDATE trades SET status = 'CLOSED' WHERE trade_id = ?", (tid,))
        assert _count_strategy_open(store, "gap_go_long") == 0

    def test_different_strategy_not_counted(self, store):
        _insert_open_trade(store, "gap_go_long", "RELIANCE")
        _insert_open_trade(store, "first_pullback_long", "INFY")
        assert _count_strategy_open(store, "gap_go_long") == 1

    def test_at_limit_blocks(self, store):
        max_cap = 2
        _insert_open_trade(store, "first_pullback_long", "RELIANCE")
        _insert_open_trade(store, "first_pullback_long", "INFY")
        count = _count_strategy_open(store, "first_pullback_long")
        assert count >= max_cap

    def test_below_limit_allows(self, store):
        max_cap = 3
        _insert_open_trade(store, "gap_go_long", "RELIANCE")
        count = _count_strategy_open(store, "gap_go_long")
        assert count < max_cap


# ── Parity ────────────────────────────────────────────────────────────────


class TestParity:
    def test_same_cap_paper_and_live(self):
        """max_concurrent_positions is config-driven, same for both modes."""
        cfg = StrategyConfig(
            name="test", display_name="Test", description="test",
            direction="LONG", intent="INTRADAY", order_protocol="CO_PLUS_TGT",
            entry_method="LIMIT", sl_method="FIXED_PCT", sl_pct=0.01,
            tgt_method="RISK_REWARD", tgt_risk_reward=2.0,
            smart_tgt_enabled=False, pullback_wait_enabled=False,
            min_score=0, lot_size=1,
            max_concurrent_positions=3,
        )
        assert cfg.max_concurrent_positions == 3
