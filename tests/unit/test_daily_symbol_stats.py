# tests/unit/test_daily_symbol_stats.py — M-S4 pre-market cache helpers (schema v44)
#
# The daily_symbol_stats table + its state_store helpers are the O(1) READ side of the
# M-S4 fix: the pre-market cron upserts, the screener reads. Pure storage; nothing wired
# to the live path yet (byte-identical until GATE 1).
from __future__ import annotations

import pytest

from core.state_store import StateStore


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "dss.db")
    try:
        yield s
    finally:
        s.close()


def test_upsert_and_read(store):
    store.upsert_daily_symbol_stats(
        "RELIANCE", "2026-07-13",
        prev_close=100.0, avg_volume_20d=50000.0, atr14=2.5, rsi14=55.0,
    )
    row = store.get_daily_symbol_stats("RELIANCE", "2026-07-13")
    assert row is not None
    assert row["symbol"] == "RELIANCE" and row["trading_date"] == "2026-07-13"
    assert row["prev_close"] == 100.0
    assert row["avg_volume_20d"] == 50000.0
    assert row["atr14"] == 2.5
    assert row["rsi14"] == 55.0
    assert row["computed_at"]  # stamped


def test_miss_returns_none(store):
    # cache MISS -> None (the screener degrades to today's 0.0/0.5 on this)
    assert store.get_daily_symbol_stats("NOPE", "2026-07-13") is None
    # right symbol, wrong date is also a miss (anti-staleness: reads are per trading_date)
    store.upsert_daily_symbol_stats("X", "2026-07-13", atr14=1.0)
    assert store.get_daily_symbol_stats("X", "2026-07-14") is None


def test_upsert_is_idempotent_on_pk(store):
    store.upsert_daily_symbol_stats("X", "2026-07-13", atr14=1.0)
    store.upsert_daily_symbol_stats("X", "2026-07-13", atr14=2.0)  # replace same PK
    row = store.get_daily_symbol_stats("X", "2026-07-13")
    assert row["atr14"] == 2.0
    with store.transaction() as cur:
        cur.execute("SELECT COUNT(*) FROM daily_symbol_stats WHERE symbol = 'X'")
        assert cur.fetchone()[0] == 1  # PK(symbol,trading_date) deduped


def test_nulls_allowed(store):
    # a stat that could not be computed is stored as NULL, not fabricated
    store.upsert_daily_symbol_stats("Y", "2026-07-13")  # all stats None
    row = store.get_daily_symbol_stats("Y", "2026-07-13")
    assert row is not None
    assert row["prev_close"] is None
    assert row["avg_volume_20d"] is None
    assert row["atr14"] is None
    assert row["rsi14"] is None
