"""Regime Phase 0 — index-candle ingestion in scripts/fetch_daily_candles.py.

Proves: the configured index universe is fetched via the SAME historical_data path as
stocks and stored in the candles table (volume=0 handled); a failing index fetch is
fail-safe (logs + continues, never raises); and the traded-stock path is unchanged
(indices are fetched additively, before the stock fetch, and never touch it).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from core import db_connect
import scripts.fetch_daily_candles as fdc


# ── fakes ─────────────────────────────────────────────────────────────────────
def _mk_candles(base: float, n: int = 3):
    """n 1-min index candles from 09:15, volume=0 (indices carry no volume)."""
    out = []
    for i in range(n):
        px = base + i
        out.append({
            "date": datetime(2026, 7, 16, 9, 15 + i, 0),
            "open": px, "high": px + 1, "low": px - 1, "close": px + 0.5,
            "volume": 0,
        })
    return out


class _FakeKite:
    """historical_data returns candles per token; can be told to raise for one token."""
    def __init__(self, raise_for_token=None):
        self.calls = []
        self._raise_for = raise_for_token

    def historical_data(self, instrument_token, from_date, to_date, interval):
        self.calls.append(instrument_token)
        assert interval == "minute"
        if instrument_token == self._raise_for:
            raise RuntimeError("simulated Kite historical_data failure")
        return _mk_candles(base=100.0 + instrument_token % 10)


_INST_MAP = {
    "NIFTY 50": 256265, "NIFTY BANK": 260105, "NIFTY IT": 259849,
}


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A temp trading_system.db + attached analytics.db with the candles table."""
    db = tmp_path / "trading_system.db"
    sqlite3.connect(str(db)).close()          # create the main file
    db_connect.init_analytics_schema(db)       # create analytics.db + candles
    monkeypatch.setattr(fdc, "DB_PATH", db)
    return db


def _candle_rows(db, token):
    conn = db_connect.connect(db)
    try:
        return conn.execute(
            "SELECT symbol, open, high, low, close, volume, interval_sec, is_synthetic "
            "FROM candles WHERE instrument_token = ? ORDER BY ts", (token,),
        ).fetchall()
    finally:
        conn.close()


# ── the config loader ─────────────────────────────────────────────────────────
def test_load_index_universe_reads_real_config():
    names = fdc._load_index_universe()
    assert "NIFTY 50" in names            # the primary regime driver must be present
    assert len(names) >= 5                # the sector set

def test_load_index_universe_missing_file_is_failsafe(monkeypatch, tmp_path):
    monkeypatch.setattr(fdc, "INDEX_UNIVERSE_PATH", tmp_path / "nope.yaml")
    assert fdc._load_index_universe() == []


# ── the fetch+store ───────────────────────────────────────────────────────────
def test_fetch_indices_stores_rows_with_zero_volume(temp_db, monkeypatch):
    monkeypatch.setattr(fdc, "_load_index_universe",
                        lambda: ["NIFTY 50", "NIFTY BANK", "NIFTY IT"])
    kite = _FakeKite()
    fdc._fetch_indices("2026-07-16", kite, _INST_MAP)

    rows = _candle_rows(temp_db, 256265)
    assert len(rows) == 3, "NIFTY 50 candles must be stored"
    assert rows[0][0] == "NIFTY 50"
    assert all(r[5] == 0 for r in rows), "index candles carry volume=0"
    assert all(r[6] == 60 for r in rows), "1-min (interval_sec=60)"
    assert all(r[7] == 0 for r in rows), "not synthetic"
    # all three indices fetched
    assert set(kite.calls) == {256265, 260105, 259849}
    assert len(_candle_rows(temp_db, 260105)) == 3


def test_fetch_indices_failsafe_one_index_fails(temp_db, monkeypatch):
    """A failing index fetch must NOT raise and must NOT stop the others."""
    monkeypatch.setattr(fdc, "_load_index_universe",
                        lambda: ["NIFTY 50", "NIFTY BANK", "NIFTY IT"])
    kite = _FakeKite(raise_for_token=260105)   # NIFTY BANK fails
    fdc._fetch_indices("2026-07-16", kite, _INST_MAP)   # must not raise

    assert len(_candle_rows(temp_db, 256265)) == 3       # NIFTY 50 stored
    assert len(_candle_rows(temp_db, 260105)) == 0       # NIFTY BANK failed, no rows
    assert len(_candle_rows(temp_db, 259849)) == 3       # NIFTY IT still stored (loop continued)


def test_fetch_indices_unknown_symbol_skipped(temp_db, monkeypatch):
    monkeypatch.setattr(fdc, "_load_index_universe", lambda: ["NIFTY 50", "NIFTY GHOST"])
    kite = _FakeKite()
    fdc._fetch_indices("2026-07-16", kite, _INST_MAP)   # GHOST not in inst_map
    assert len(_candle_rows(temp_db, 256265)) == 3
    assert kite.calls == [256265]                        # ghost never fetched


def test_fetch_indices_empty_universe_is_noop(temp_db, monkeypatch):
    monkeypatch.setattr(fdc, "_load_index_universe", lambda: [])
    kite = _FakeKite()
    fdc._fetch_indices("2026-07-16", kite, _INST_MAP)
    assert kite.calls == []


# ── the stock path is unchanged (indices are additive + fail-safe) ─────────────
def test_fetch_single_day_runs_indices_then_stock_path(temp_db, monkeypatch):
    """_fetch_single_day must call _fetch_indices, then reach the stock path exactly as
    before. With no traded symbols it returns after the (unchanged) skip — proving the
    index fetch did not alter stock-candle behaviour."""
    called = {"indices": False, "traded": False}

    def _fake_indices(td, kite, im):
        called["indices"] = True

    def _fake_traded(date_iso):
        called["traded"] = True
        return []   # no PROCESSED signals → stock path early-returns as before

    monkeypatch.setattr(fdc, "_fetch_indices", _fake_indices)
    monkeypatch.setattr(fdc, "_get_traded_symbols", _fake_traded)
    fdc._fetch_single_day("2026-07-16", _FakeKite(), _INST_MAP)

    assert called["indices"] is True, "indices fetched"
    assert called["traded"] is True, "stock path still reached (unchanged)"
